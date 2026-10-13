"""Talk to `grok agent stdio` using the Agent Client Protocol (ACP).

Reused from grok-app-studio/acp_client.py (itself from gridos-companion), extended for Grok Desktop:
  * remembers the agent's capabilities from `initialize` (real grok 1.0.46: promptCapabilities.image=false,
    embeddedContext=true, loadSession=true, sessionCapabilities {list, resume, close}) and its models
    (`_meta.modelState.availableModels` / `currentModelId`);
  * open_session(): `session/resume` (no replay) -> `session/load` (replay suppressed) -> `session/new`;
  * prompt() takes a list of ACP content blocks (text, resource = embedded file, image when supported);
  * set_model(): `session/set_model`, caller falls back to restarting grok with `--model`;
  * kill(): emergency stop, kills the grok process at once.

ACP is JSON-RPC 2.0, one JSON object per line on stdin/stdout. We never send yoloMode/always-approve and
never pick grok's "always approve" options; every permission request is answered one by one.
"""
from __future__ import annotations

import collections
import json
import os
import time

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal, Slot

from . import APP_NAME, __version__

PROTOCOL_VERSION = 1
CLIENT_TYPE = "grok-desktop"


class AcpClient(QObject):
    stateChanged = Signal(str)           # stopped | starting | needs_login | connecting | ready | busy | error
    needsLogin = Signal(str)
    sessionStarted = Signal(str, str)    # sessionId, how: new | resumed | loaded
    messageChunk = Signal(str)
    thoughtChunk = Signal(str)
    toolCallChanged = Signal(dict)
    planChanged = Signal(list)
    permissionRequested = Signal(object, dict)
    questionRequested = Signal(object, dict)
    turnFinished = Signal(str)
    errorOccurred = Signal(str)
    logLine = Signal(str)
    agentInfo = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: QProcess | None = None
        self._buf = b""
        self._next_id = 1
        self._pending: dict[int, tuple[str, object]] = {}
        self._tool_calls: dict[str, dict] = {}
        self.session_id: str | None = None
        self.state = "stopped"
        self.auth_methods: list[dict] = []
        self.capabilities: dict = {}
        self.models: list[dict] = []
        self.current_model = ""
        self.agent_version = ""
        self.cwd = os.path.expanduser("~")
        self.rules = ""
        self.mcp_servers: list[dict] = []
        self.model = ""
        self.loading = False
        self._resume_id = ""
        self._turn_id: int | None = None
        self._turn_t0 = 0.0
        self._turn_chunks = 0
        self.last_traffic = time.time()      # last time grok sent us anything (watchdog)
        self.trace: collections.deque = collections.deque(maxlen=120)   # (time, '<'|'>', summary) of recent ACP messages

    # ------------------------------------------------------------ process --
    def _set_state(self, s: str) -> None:
        if s != self.state:
            self.state = s
            self.stateChanged.emit(s)

    def start(self, grok: str, cwd: str, rules: str = "", mcp_servers: list[dict] | None = None, model: str = "",
              resume_id: str = "") -> None:
        self.stop()
        self.cwd, self.rules, self.mcp_servers, self.model = cwd, rules, mcp_servers or [], model
        self._resume_id = resume_id
        os.makedirs(cwd, exist_ok=True)
        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("NO_COLOR", "1")
        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(cwd)
        args = ["agent"] + (["--model", model] if model else []) + ["stdio"]
        self.proc.readyReadStandardOutput.connect(self._on_stdout)
        self.proc.readyReadStandardError.connect(self._on_stderr)
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(self._on_proc_error)
        self._set_state("starting")
        self.logLine.emit(f"$ {grok} {' '.join(args)}")
        self.proc.start(grok, args)
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            # grok uses its own file/terminal tools (+ our MCP server); we don't offer ours over ACP.
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": CLIENT_TYPE, "title": APP_NAME, "version": __version__},
            "_meta": {"clientType": CLIENT_TYPE, "clientVersion": __version__},
        }, self._on_initialized)

    def stop(self) -> None:
        if self.proc is not None:
            p, self.proc = self.proc, None
            try:
                p.finished.disconnect(self._on_finished)
            except (RuntimeError, TypeError):
                pass
            p.closeWriteChannel()
            if not p.waitForFinished(1500):
                p.terminate()
                if not p.waitForFinished(1500):
                    p.kill()
                    p.waitForFinished(1000)
            p.deleteLater()
        self._reset()
        self._set_state("stopped")

    def kill(self) -> None:
        """Emergency stop: no goodbye, kill grok now (it also takes its MCP servers down)."""
        if self.proc is not None:
            p, self.proc = self.proc, None
            try:
                p.finished.disconnect(self._on_finished)
            except (RuntimeError, TypeError):
                pass
            p.kill()
            p.waitForFinished(2000)
            p.deleteLater()
        busy = self._turn_id is not None
        if busy:
            self.logLine.emit(f"prompt #{self._turn_id} aborted: grok killed after {time.time() - self._turn_t0:.1f}s")
        self._reset()
        if busy:
            self.turnFinished.emit("cancelled")
        self._set_state("stopped")

    def _reset(self) -> None:
        self._pending.clear()
        self._tool_calls.clear()
        self.session_id = None
        self._turn_id = None
        self._buf = b""
        self.loading = False

    # ------------------------------------------------------------ JSON-RPC --
    def _trace(self, d: str, msg: dict) -> None:
        what = msg.get("method") or ("result" if "result" in msg else "error" if "error" in msg else "?")
        if what == "session/update":
            what += ":" + str(((msg.get("params") or {}).get("update") or {}).get("sessionUpdate"))
        if msg.get("id") is not None:
            what += f" #{msg['id']}"
        self.trace.append((time.time(), d, what))

    def pending_summary(self) -> list[str]:
        return [f"#{k} {v[0]}" for k, v in self._pending.items()]

    def _write(self, obj: dict) -> None:
        if self.proc is not None:
            self._trace(">", obj)
            self.proc.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    def _request(self, method: str, params: dict, callback=None) -> int:
        mid = self._next_id
        self._next_id += 1
        self._pending[mid] = (method, callback)
        self._write({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        return mid

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _reply(self, mid, result=None, error=None) -> None:
        msg = {"jsonrpc": "2.0", "id": mid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result if result is not None else {}
        self._write(msg)

    @Slot()
    def _on_stdout(self) -> None:
        if self.proc is None:
            return
        self._buf += bytes(self.proc.readAllStandardOutput())
        self.last_traffic = time.time()
        buf, pos = self._buf, 0
        # walk the buffer with an index (re-slicing it per line is quadratic for a big burst of notifications)
        while True:
            nl = buf.find(b"\n", pos)
            if nl < 0:
                break
            line, pos = buf[pos:nl].strip(), nl + 1
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.logLine.emit("grok (non-JSON): " + line.decode("utf-8", "replace")[:300])
                continue
            self._trace("<", msg)
            try:
                self._dispatch(msg)
            except Exception as e:  # noqa: BLE001  one bad message must never wedge the reader
                self.logLine.emit(f"error handling ACP message {str(msg)[:200]}: {e!r}")
            if self.proc is None:      # a handler restarted/stopped grok
                return
        self._buf = buf[pos:]

    @Slot()
    def _on_stderr(self) -> None:
        if self.proc is None:
            return
        for l in bytes(self.proc.readAllStandardError()).decode("utf-8", "replace").splitlines():
            if l.strip():
                self.logLine.emit("grok: " + l)

    @Slot(int, QProcess.ExitStatus)
    def _on_finished(self, code, _status) -> None:
        self.logLine.emit(f"grok exited with code {code}")
        was_busy = self._turn_id is not None
        if was_busy:
            self.logLine.emit(f"prompt #{self._turn_id} aborted: grok exited mid-turn after {time.time() - self._turn_t0:.1f}s")
        self.proc = None
        self._reset()
        if was_busy:
            self.turnFinished.emit("error")
        if self.state not in ("needs_login", "stopped"):
            self.errorOccurred.emit(f"grok stopped unexpectedly (exit code {code}).")
            self._set_state("error")

    @Slot(QProcess.ProcessError)
    def _on_proc_error(self, err) -> None:
        if err == QProcess.ProcessError.FailedToStart:
            self.errorOccurred.emit("Couldn't start grok. Is it installed?")
            self._set_state("error")

    def _dispatch(self, msg: dict) -> None:
        if "method" in msg:
            if "id" in msg:
                self._handle_request(msg["id"], msg["method"], msg.get("params") or {})
            else:
                self._handle_notification(msg["method"], msg.get("params") or {})
            return
        method, cb = self._pending.pop(msg.get("id"), (None, None))
        if cb is not None:
            cb(msg.get("result"), msg.get("error"))

    # ------------------------------------------------------- agent -> us ----
    def _handle_request(self, mid, method: str, params: dict) -> None:
        if method == "session/request_permission":
            p = dict(params)
            p["toolCall"] = self._merge_tool_call(dict(params.get("toolCall") or {}))
            self.permissionRequested.emit(mid, p)
        elif method in ("x.ai/ask_user_question", "_x.ai/ask_user_question"):
            inner = params.get("params", params) if method.startswith("_") else params
            self.questionRequested.emit(mid, inner)
        elif method in ("x.ai/folder_trust/request", "_x.ai/folder_trust/request"):
            self._reply(mid, {"outcome": "reject"})  # capability never advertised
        else:
            self._reply(mid, error={"code": -32601, "message": f"Method not supported by {APP_NAME}: {method}"})

    def _handle_notification(self, method: str, params: dict) -> None:
        if method != "session/update" or self.loading:
            return
        if self.session_id and params.get("sessionId") not in (None, self.session_id):
            return  # an old session's late updates
        u = params.get("update") or {}
        kind = u.get("sessionUpdate")
        if kind == "agent_message_chunk":
            c = u.get("content") or {}
            if c.get("type", "text") == "text" and c.get("text"):
                self.messageChunk.emit(c["text"])
        elif kind == "agent_thought_chunk":
            c = u.get("content") or {}
            if c.get("text"):
                self.thoughtChunk.emit(c["text"])
        elif kind in ("tool_call", "tool_call_update"):
            self.toolCallChanged.emit(self._merge_tool_call(u))
        elif kind == "plan":
            self.planChanged.emit(u.get("entries") or [])
        elif kind == "current_model_update" and u.get("modelId"):
            self.current_model = u["modelId"]

    def _merge_tool_call(self, u: dict) -> dict:
        tid = u.get("toolCallId") or ""
        cur = self._tool_calls.setdefault(tid, {"toolCallId": tid, "status": "pending"})
        for k, v in u.items():
            if k != "sessionUpdate" and v is not None:
                cur[k] = v
        return dict(cur)

    # ------------------------------------------------------------ us -> agent --
    def _on_initialized(self, result, error) -> None:
        if error:
            self.errorOccurred.emit(f"grok refused to start: {error.get('message')}")
            self._set_state("error")
            return
        result = result or {}
        self.auth_methods = result.get("authMethods") or []
        self.capabilities = result.get("agentCapabilities") or {}
        meta = result.get("_meta") or {}
        ms = meta.get("modelState") or {}
        self.models = [m for m in ms.get("availableModels") or [] if isinstance(m, dict) and m.get("modelId")]
        self.current_model = self.model or ms.get("currentModelId", "")
        self.agent_version = meta.get("agentVersion", "")
        self.agentInfo.emit({"version": self.agent_version, "model": self.current_model, "models": self.models,
                             "authMethods": [m.get("id") for m in self.auth_methods],
                             "image": self.supports_images})
        if "cached_token" in [m.get("id") for m in self.auth_methods]:
            self._set_state("connecting")
            self._request("authenticate", {"methodId": "cached_token"}, self._on_authenticated)
        else:  # only interactive sign-in offered (we never use xai.api_key: this app is for the SuperGrok login)
            self._set_state("needs_login")
            self.needsLogin.emit("You're not signed in to grok yet.")

    def _on_authenticated(self, _result, error) -> None:
        if error:
            self._set_state("needs_login")
            self.needsLogin.emit(f"Your grok sign-in has expired ({error.get('message', 'auth failed')}).")
            return
        rid, self._resume_id = self._resume_id, ""
        self.open_session(rid)

    @property
    def supports_images(self) -> bool:
        return bool((self.capabilities.get("promptCapabilities") or {}).get("image"))

    @property
    def supports_embedded(self) -> bool:
        return bool((self.capabilities.get("promptCapabilities") or {}).get("embeddedContext"))

    def _can_resume(self) -> bool:
        return "resume" in (self.capabilities.get("sessionCapabilities") or {})

    def open_session(self, resume_id: str = "") -> None:
        """Reopen `resume_id` if possible (resume -> load), else start a new session."""
        if self.proc is None or self._turn_id is not None:
            return
        self._tool_calls.clear()
        self.session_id = None
        self._set_state("connecting")
        if resume_id and self._can_resume():
            self._request("session/resume", {"sessionId": resume_id, "cwd": self.cwd, "mcpServers": self.mcp_servers},
                          lambda r, e: self._on_reopened(resume_id, "resumed", r, e))
        elif resume_id and self.capabilities.get("loadSession"):
            self._load(resume_id)
        else:
            self._new()

    def _load(self, sid: str) -> None:
        self.loading = True  # grok replays the old conversation; we already show it from our own history
        self._request("session/load", {"sessionId": sid, "cwd": self.cwd, "mcpServers": self.mcp_servers},
                      lambda r, e: self._on_reopened(sid, "loaded", r, e))

    def _new(self) -> None:
        self._request("session/new", {"cwd": self.cwd, "mcpServers": self.mcp_servers,
                                      "_meta": {"rules": self.rules, "yoloMode": False}}, self._on_session)

    def _on_reopened(self, sid, how, result, error) -> None:
        self.loading = False
        if error:
            self.logLine.emit(f"session/{'resume' if how == 'resumed' else 'load'} {sid} failed: {error.get('message', '')}")
            if how == "resumed" and self.capabilities.get("loadSession"):
                self._load(sid)
            else:
                self._new()
            return
        self._started(sid, how)

    def _on_session(self, result, error) -> None:
        if error:
            msg = error.get("message", "")
            if "auth" in msg.lower():
                self._set_state("needs_login")
                self.needsLogin.emit("grok says you need to sign in.")
            else:
                self.errorOccurred.emit(f"Couldn't start a chat: {msg}")
                self._set_state("error")
            return
        result = result or {}
        cm = (result.get("models") or {}).get("currentModelId")
        if cm:
            self.current_model = cm
        self._started(result.get("sessionId") or "", "new")

    def _started(self, sid: str, how: str) -> None:
        self.session_id = sid
        self._set_state("ready")
        self.sessionStarted.emit(sid, how)

    def prompt(self, blocks: list[dict] | str) -> bool:
        if not self.session_id or self._turn_id is not None:
            return False
        if isinstance(blocks, str):
            blocks = [{"type": "text", "text": blocks}]
        self._turn_id = self._request("session/prompt", {"sessionId": self.session_id, "prompt": blocks},
                                      self._on_prompt_done)
        self._turn_t0 = self.last_traffic = time.time()
        self.logLine.emit(f"prompt #{self._turn_id} start session={self.session_id} blocks={len(blocks)} "
                          f"chars={sum(len(b.get('text', '')) for b in blocks if isinstance(b, dict))} pending={self.pending_summary()}")
        self._set_state("busy")
        return True

    def _on_prompt_done(self, result, error) -> None:
        tid, self._turn_id = self._turn_id, None
        self.logLine.emit(f"prompt #{tid} end stopReason={(result or {}).get('stopReason')} "
                          f"error={(error or {}).get('message') if error else None} took={time.time() - self._turn_t0:.1f}s "
                          f"pending={self.pending_summary()}")
        if self.proc is not None:
            self._set_state("ready")
        if error:
            msg = error.get("message", "unknown error")
            if "auth" in msg.lower():
                self._set_state("needs_login")
                self.needsLogin.emit("grok says your sign-in expired.")
            else:
                self.errorOccurred.emit(f"grok error: {msg}")
            self.turnFinished.emit("error")
            return
        self.turnFinished.emit((result or {}).get("stopReason", "end_turn"))

    def cancel(self) -> None:
        if self.session_id and self._turn_id is not None:
            self._notify("session/cancel", {"sessionId": self.session_id})

    def set_model(self, model_id: str, callback=None) -> None:
        """Switch model for the current session. callback(ok: bool)."""
        self.model = model_id
        if not self.session_id:
            if callback:
                callback(False)
            return

        def done(_r, e):
            if not e:
                self.current_model = model_id
            if callback:
                callback(not e)
        self._request("session/set_model", {"sessionId": self.session_id, "modelId": model_id}, done)

    def respond_permission(self, mid, outcome: dict) -> None:
        self._reply(mid, {"outcome": outcome})

    def respond_question(self, mid, answers: dict | None) -> None:
        self._reply(mid, {"outcome": "cancelled"} if answers is None else {"outcome": "accepted", "answers": answers})

    @property
    def busy(self) -> bool:
        return self._turn_id is not None
