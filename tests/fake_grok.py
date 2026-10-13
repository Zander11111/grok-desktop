#!/usr/bin/env python3
"""A stand-in for xAI's `grok` CLI for tests and screenshots (no account, no network).

Extended from grok-app-studio/tests/fake_grok.py (itself from gridos-companion's mock_grok.py).
Matches what the real grok 1.0.46 says in `initialize` (promptCapabilities.image=false, embeddedContext=true,
loadSession, sessionCapabilities {list, resume, close}, models in _meta.modelState), and it really starts the
MCP servers the client passes in session/new and calls their tools, so the whole computer-control path runs.

  grok --version | grok login [--device-auth] | grok agent [--model M] stdio

Prompt keywords: "slow" (slow stream, for Stop), "screenshot", "click", "type <text>", "list <dir>",
"delete <path>", "write <path>", "run <command>", "sneaky delete <path>" (MCP call WITHOUT asking over ACP),
"drag" (long mouse drag, for the emergency stop), "what did i say" (recalls the session's history).
State: $MOCK_GROK_HOME: logged_in marker, sessions.json, acp-received.jsonl (everything the client sent).
Flags (env MOCK_<NAME>=1 or a file <name> in $MOCK_GROK_HOME): resume_fail, no_set_model; env MOCK_IMAGES=1.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid

VERSION = "grok 1.0.46 (fake)"
HOME = os.environ.get("MOCK_GROK_HOME") or os.path.expanduser("~/.grok-mock")
os.makedirs(HOME, exist_ok=True)
MARKER = os.path.join(HOME, "logged_in")
RECV_LOG = os.path.join(HOME, "acp-received.jsonl")
SESSIONS = os.path.join(HOME, "sessions.json")
SPEED = float(os.environ.get("MOCK_GROK_SPEED", "1"))
MODELS = [{"modelId": "grok-4.6", "name": "Grok 4.6", "description": "latest frontier model"},
          {"modelId": "grok-4.5", "name": "Grok 4.5"}]


def flag(name: str) -> bool:
    """MOCK_<NAME> env var, or a file <name> in MOCK_GROK_HOME (so tests can flip it while grok runs)."""
    return bool(os.environ.get("MOCK_" + name.upper())) or os.path.exists(os.path.join(HOME, name))


def logged_in() -> bool:
    return os.path.exists(MARKER)


def load_sessions() -> dict:
    try:
        return json.load(open(SESSIONS))
    except (OSError, ValueError):
        return {}


def save_sessions(s: dict) -> None:
    json.dump(s, open(SESSIONS, "w"), indent=1)


def cmd_login(args):
    code = "FAKE-" + uuid.uuid4().hex[:4].upper()
    print(f"To sign in, open this URL in your browser: https://accounts.x.ai/oauth2/device?user_code={code}")
    print(f"Confirm this code in your browser: {code}")
    print("Waiting for authorization...", flush=True)
    delay = float(os.environ.get("MOCK_LOGIN_DELAY", "1.0"))
    if delay < 0:
        while True:
            time.sleep(3600)
    time.sleep(delay)
    open(MARKER, "w").write("fake-user")
    print("Logged in successfully.")
    return 0


class Mcp:
    """Minimal MCP stdio client for the servers the ACP client hands us."""

    def __init__(self, spec: dict):
        env = dict(os.environ)
        for e in spec.get("env") or []:
            env[e["name"]] = e["value"]
        self.name = spec["name"]
        self.p = subprocess.Popen([spec["command"], *spec.get("args", [])], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=open(os.path.join(HOME, f"mcp-{self.name}.log"), "a"),
                                  env=env, text=True, encoding="utf-8")
        self.n = 0
        init = self.req("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "fake-grok", "version": "1"}})
        self.server_info = init.get("result", {}).get("serverInfo", {})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()
        self.tools = [t["name"] for t in self.req("tools/list", {}).get("result", {}).get("tools", [])]

    def req(self, method, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                return {"error": {"message": "mcp server died"}}
            msg = json.loads(line)
            if msg.get("id") == self.n:
                return msg

    def call(self, tool, args):
        return self.req("tools/call", {"name": tool, "arguments": args}).get("result") or {}


class Acp:
    def __init__(self, model: str):
        self.q: queue.Queue = queue.Queue()
        self.next_id = 1000
        self.authed = False
        self.model = model or "grok-4.6"
        self.sessions = load_sessions()
        self.mcp: dict[str, Mcp] = {}
        self.cancelled = False
        self.lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            with open(RECV_LOG, "a") as f:
                f.write(json.dumps(msg) + "\n")
            self.q.put(msg)
        self.q.put(None)

    def send(self, obj):
        with self.lock:
            sys.stdout.write(json.dumps(obj) + "\n")
            sys.stdout.flush()

    def reply(self, mid, result=None, error=None):
        m = {"jsonrpc": "2.0", "id": mid}
        if error:
            m["error"] = error
        else:
            m["result"] = result if result is not None else {}
        self.send(m)

    def update(self, sid, upd):
        self.send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": upd}})

    def request(self, method, params):
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
        return self.next_id

    def wait_response(self, rid):
        while True:
            msg = self.q.get()
            if msg is None:
                self.shutdown()
            if msg.get("id") == rid and "method" not in msg:
                return msg
            if msg.get("method") == "session/cancel":
                self.cancelled = True

    def pause(self, s):
        end = time.time() + s / SPEED
        while time.time() < end:
            try:
                msg = self.q.get(timeout=max(0.0, min(0.05, end - time.time())))
            except queue.Empty:
                continue
            if msg is None:
                self.shutdown()
            if msg.get("method") == "session/cancel":
                self.cancelled = True

    def stream(self, sid, kind, text, delay=0.02):
        for i, word in enumerate(text.split(" ")):
            if self.cancelled:
                return
            self.update(sid, {"sessionUpdate": kind, "content": {"type": "text", "text": word if i == 0 else " " + word}})
            self.pause(delay)

    def shutdown(self):
        for m in self.mcp.values():
            m.p.kill()
        sys.exit(0)

    def ensure_mcp(self, servers):
        for spec in servers or []:
            if spec.get("name") not in self.mcp and spec.get("command"):
                try:
                    self.mcp[spec["name"]] = Mcp(spec)
                except Exception as e:  # noqa: BLE001
                    print(f"fake grok: MCP {spec.get('name')} failed: {e}", file=sys.stderr)

    def run(self):
        while True:
            msg = self.q.get()
            if msg is None:
                self.shutdown()
            method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
            if method == "initialize":
                methods = [{"id": "grok.com", "name": "Grok", "description": "Sign in with Grok"}]
                if logged_in():
                    methods.insert(0, {"id": "cached_token", "name": "Cached token", "description": "Use saved login"})
                self.reply(mid, {"protocolVersion": 1, "agentCapabilities": {
                    "loadSession": True,
                    "promptCapabilities": {"image": bool(os.environ.get("MOCK_IMAGES")), "audio": False, "embeddedContext": True},
                    "mcpCapabilities": {"http": True, "sse": True},
                    "sessionCapabilities": {"list": {}, "resume": {}, "close": {}}},
                    "authMethods": methods,
                    "_meta": {"agentVersion": "1.0.46-fake",
                              "modelState": {"currentModelId": self.model, "availableModels": MODELS}}})
            elif method == "authenticate":
                if params.get("methodId") == "cached_token" and logged_in():
                    self.authed = True
                    self.reply(mid, {})
                else:
                    self.reply(mid, error={"code": -32000, "message": "Authentication required"})
            elif method in ("session/new", "session/load", "session/resume"):
                if not self.authed:
                    self.reply(mid, error={"code": -32000, "message": "Authentication required"})
                    continue
                self.ensure_mcp(params.get("mcpServers"))
                if method == "session/new":
                    sid = str(uuid.uuid4())
                    self.sessions[sid] = {"turns": 0, "history": [], "model": self.model}
                    save_sessions(self.sessions)
                    self.reply(mid, {"sessionId": sid, "models": {"currentModelId": self.model, "availableModels": MODELS}})
                    continue
                sid = params.get("sessionId")
                if flag("resume_fail") or sid not in self.sessions:
                    self.reply(mid, error={"code": -32602, "message": f"session not found: {sid}"})
                    continue
                if method == "session/load":  # replays history
                    for h in self.sessions[sid]["history"]:
                        self.update(sid, {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": h}})
                self.reply(mid, {})
            elif method == "session/set_model":
                if flag("no_set_model"):
                    self.reply(mid, error={"code": -32601, "message": "method not found: session/set_model"})
                    continue
                self.model = params.get("modelId", self.model)
                self.reply(mid, {})
            elif method == "session/list":
                self.reply(mid, {"sessions": [{"sessionId": k} for k in self.sessions]})
            elif method == "session/prompt":
                sid = params.get("sessionId")
                s = self.sessions.get(sid)
                if s is None:
                    self.reply(mid, error={"code": -32602, "message": "unknown session"})
                    continue
                self.cancelled = False
                blocks = params.get("prompt") or []
                text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                reason = self.turn(sid, s, text, blocks)
                s["turns"] += 1
                s["history"].append(text)
                save_sessions(self.sessions)
                self.reply(mid, {"stopReason": "cancelled" if self.cancelled else reason})
            elif method == "session/cancel":
                pass
            elif mid is not None:
                self.reply(mid, error={"code": -32601, "message": f"method not found: {method}"})

    # ---------------------------------------------------------------- turns --
    OPTS = [{"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
            {"optionId": "always-allow", "name": "Always allow", "kind": "allow_always"},
            {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"}]

    def ask(self, sid, tc):
        self.update(sid, {"sessionUpdate": "tool_call", **tc, "status": "pending"})
        rid = self.request("session/request_permission", {"sessionId": sid, "toolCall": {"toolCallId": tc["toolCallId"]},
                                                          "options": self.OPTS})
        out = ((self.wait_response(rid).get("result") or {}).get("outcome") or {})
        return out.get("outcome") == "selected" and out.get("optionId", "").startswith("allow") and not self.cancelled

    def done(self, sid, tid, ok, text=""):
        self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": tid, "status": "completed" if ok else "failed",
                          "content": [{"type": "content", "content": {"type": "text", "text": text[:500]}}]})

    def mcp_tool(self, sid, tool, args, ask=True):
        """Ask over ACP like the real grok does for MCP tools, then call the tool. -> (ok, result)"""
        m = self.mcp.get("grok-desktop")
        if m is None:
            self.stream(sid, "agent_message_chunk", "(I don't have the grok-desktop tools.) ")
            return False, {}
        if tool == "powershell" and "shell" in getattr(m, "tools", []):   # the Linux tool set calls it "shell"
            tool = "shell"
        tid = "call_" + uuid.uuid4().hex[:6]
        pretty = "(grok-desktop) " + tool.replace("_", " ").title()
        tc = {"toolCallId": tid, "title": pretty, "kind": "other",
              "rawInput": {"tool_name": f"grok-desktop__{tool}", "tool_input": args}}
        if ask:
            if not self.ask(sid, tc):
                self.done(sid, tid, False, "denied")
                return False, {"denied_by_acp": True}
        else:
            self.update(sid, {"sessionUpdate": "tool_call", **tc, "status": "in_progress"})
        res = m.call(tool, args)
        sc = res.get("structuredContent") or {}
        self.done(sid, tid, not res.get("isError"), sc.get("message", ""))
        return not res.get("isError"), res

    def turn(self, sid, s, text, blocks) -> str:
        low = text.lower()
        say = lambda t, d=0.01: self.stream(sid, "agent_message_chunk", t, d)  # noqa: E731
        self.stream(sid, "agent_thought_chunk", "Thinking about what the user asked.", 0.002)
        if "hangforever" in low:   # agent wedges: streams a bit, then never answers (and ignores session/cancel)
            say("Starting the long job now")
            while True:
                try:
                    self.q.get(timeout=0.2)
                except queue.Empty:
                    pass
        if "flood" in low:         # a burst of ~40k notifications (8 MB) in one go, like a huge tool output stream
            for i in range(40000):
                self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "in_progress",
                                  "title": "shell", "rawOutput": "x" * 180})
            say("Flood done.")
            return "end_turn"
        if "slow" in low:
            say(" ".join(f"word{i}" for i in range(60)), 0.25)
            return "end_turn"
        if "what did i say" in low:
            prev = [h for h in s["history"]]
            if "[earlier in this conversation" in low:
                say("From the context you resent: " + text.split("[End of earlier conversation]")[0][-200:])
            elif prev:
                say("Earlier you said: " + " | ".join(p[:60] for p in prev))
            else:
                say("I don't remember anything before this.")
            return "end_turn"
        m = re.search(r"sneaky delete (\S+)", text, re.I)
        if m:
            ok, res = self.mcp_tool(sid, "fs_delete", {"path": m.group(1)}, ask=False)
            say(f"Sneaky delete result: {(res.get('structuredContent') or {}).get('message', res)}")
            return "end_turn"
        if "screenshot" in low or "click" in low:
            ok, res = self.mcp_tool(sid, "screenshot", {})
            imgs = [c for c in res.get("content") or [] if c.get("type") == "image"]
            sc = res.get("structuredContent") or {}
            if not ok:
                say(f"I couldn't take a screenshot: {sc.get('message', 'denied')}")
                return "end_turn"
            say(f"I can see your screen ({sc.get('width')}×{sc.get('height')}, {len(imgs)} image). ")
            if "click" in low:
                ok, res = self.mcp_tool(sid, "mouse_click", {"x": 640, "y": 360})
                say("Clicked the button." if ok else f"Click failed: {(res.get('structuredContent') or {}).get('message', 'denied')}")
            return "end_turn"
        if "drag" in low:
            ok, res = self.mcp_tool(sid, "mouse_drag", {"x1": 10, "y1": 10, "x2": 900, "y2": 500})
            for i in range(10):  # keeps working (dragging again) until stopped
                if self.cancelled:
                    return "cancelled"
                ok, res = self.mcp_tool(sid, "mouse_drag", {"x1": 10, "y1": 10 + i, "x2": 900, "y2": 500}, ask=False)
                self.pause(0.1)
            say("Done dragging.")
            return "end_turn"
        m = re.search(r"\btype (.+)$", text, re.I | re.M)
        if m:
            ok, res = self.mcp_tool(sid, "keyboard_type", {"text": m.group(1).strip()})
            say("Typed it." if ok else "Didn't type.")
            return "end_turn"
        m = re.search(r"\blist (\S+)", text, re.I)
        if m:
            ok, res = self.mcp_tool(sid, "fs_list", {"path": m.group(1)})
            sc = res.get("structuredContent") or {}
            say(f"{sc.get('message', 'no result')}: " + ", ".join(i["name"] for i in sc.get("items", [])))
            return "end_turn"
        m = re.search(r"\bdelete (\S+)", text, re.I)
        if m:
            ok, res = self.mcp_tool(sid, "fs_delete", {"path": m.group(1)})
            say((res.get("structuredContent") or {}).get("message", "I didn't delete it."))
            return "end_turn"
        m = re.search(r"\bwrite (\S+)", text, re.I)
        if m:
            ok, res = self.mcp_tool(sid, "fs_write", {"path": m.group(1), "content": "hello from grok\n",
                                                      "overwrite": "overwrite" in low})
            say((res.get("structuredContent") or {}).get("message", "I didn't write it."))
            return "end_turn"
        m = re.search(r"\brun (.+)$", text, re.I | re.M)
        if m:
            ok, res = self.mcp_tool(sid, "powershell", {"command": m.group(1).strip()})
            sc = res.get("structuredContent") or {}
            say(f"Output: `{(sc.get('stdout') or sc.get('message', '')).strip()}`")
            return "end_turn"
        # attachments
        notes = []
        for b in blocks:
            if b.get("type") == "resource":
                r = b.get("resource") or {}
                notes.append(f"file {os.path.basename(r.get('uri', ''))} ({len(r.get('text', ''))} chars)")
            elif b.get("type") == "image":
                notes.append(f"inline image ({b.get('mimeType')})")
        for path in re.findall(r"saved at (.+?) — look at it with the grok-desktop view_image tool", text):
            ok, res = self.mcp_tool(sid, "view_image", {"path": path.strip()})
            sc = res.get("structuredContent") or {}
            imgs = [c for c in res.get("content") or [] if c.get("type") == "image"]
            notes.append(f"image via view_image: {sc.get('message', 'denied')} ({len(imgs)} image)")
        if notes:
            say("I got your attachments: " + "; ".join(notes) + ".")
            return "end_turn"
        say(f"Here's an answer to: *{text.strip()[:80]}* (model {self.model})\n\n"
            "```python\nprint(\"hello from grok\")\n```\n\nAnything else?")
        return "end_turn"


def main(argv):
    if argv[:1] in (["--version"], ["-V"]):
        print(VERSION)
        return 0
    if argv[:1] == ["login"]:
        return cmd_login(argv[1:])
    if argv[:1] == ["agent"] and argv[-1] == "stdio":
        model = argv[argv.index("--model") + 1] if "--model" in argv else ""
        Acp(model).run()
        return 0
    print("fake grok: only --version, login and agent stdio are supported", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
