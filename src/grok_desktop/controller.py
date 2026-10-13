"""The brain of Grok Desktop (no widgets, so the tests can drive it): conversations + history, the grok ACP
client, attachments, permission decisions, the computer-control link, emergency stop, models, sign-in."""
from __future__ import annotations

import base64
import mimetypes
import os
import sys
import pathlib
import shutil
import time

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from . import APP_NAME, __version__, paths, permissions
from .acp_client import AcpClient
from .control import ControlServer
from .mcp import SERVER_NAME
from .mcp.catalog import INPUT_TOOLS
from .mcp.png import MIME as IMAGE_MIME
from .mcp.png import size_of
from .store import Store

TEXT_EXT = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".log", ".ini", ".cfg", ".toml", ".yaml", ".yml",
            ".xml", ".html", ".css", ".c", ".cpp", ".h", ".cs", ".java", ".rs", ".go", ".ps1", ".bat", ".sh", ".sql",
            ".lua", ".kt", ".swift", ".rb", ".php", ".tsx", ".jsx", ".vue", ".gd", ".cfg", ".srt"}
MAX_EMBED = 200_000

RULES = """You are Grok, running inside "Grok Desktop", a desktop chat app on the user's {pc} ({os}).
Be friendly, clear and concise; use Markdown (code in fenced blocks with a language).
You can use this PC through the "grok-desktop" MCP tools: fs_list/fs_read/fs_write/fs_move/fs_delete/fs_search,
view_image, {shell}, screenshot, screen_info, mouse_*/keyboard_*, list_windows, focus_window, open_url, open_app.
- For anything on the screen: take a screenshot first, act, then take another screenshot to check.
- Mouse coordinates are in the pixels of your latest screenshot.
- Prefer files/{shell_name} over clicking when it does the job. Never use admin rights unless the user asks.
- The user approves risky actions in the app. If a tool was denied or stopped, don't retry it on your own: ask.
- git and the GitHub CLI (gh) can be used from the shell if the user signed in via the GitHub button. Ask before pushing or creating a repository, never force-push or delete repositories, and never read or print credentials/tokens.
- When the user attaches an image you get its path: look at it with view_image.
The user's home folder is {home}. Today is {date}."""


class Controller(QObject):
    stateChanged = Signal(str)            # no_grok | stopped | starting | connecting | ready | busy | needs_login | error | logging_in | installing
    conversationsChanged = Signal()
    currentChanged = Signal(str)          # conversation id ("" = none)
    messagesChanged = Signal()
    permissionChanged = Signal()          # the head of the permission queue changed
    controlActive = Signal(bool, str)     # AI is using mouse/keyboard (indicator), description
    killedChanged = Signal(bool)
    modelsChanged = Signal()
    loginInfo = Signal(str, str)          # url, code
    notice = Signal(str)                  # short status message for the UI
    wantShow = Signal()
    assistantText = Signal(str)           # every streamed chunk of grok's reply (voice mode speaks these)
    toolStep = Signal()                   # grok started a tool step
    turnEnded = Signal(str)               # the answer finished (reason)
    stuckChanged = Signal(bool, int)      # watchdog: grok has been silent mid-turn (True, seconds) / recovered (False, 0)

    def __init__(self, settings: paths.Settings | None = None, store: Store | None = None, parent=None):
        super().__init__(parent)
        self.settings = settings or paths.Settings()
        self.store = store or Store(paths.db_path())
        self.acp = AcpClient(self)
        self.control = ControlServer(self)
        self.control.listen()
        self.state = "stopped"
        self.turn_voice = False           # the running turn was started by voice
        self.grok: str | None = None
        self.grok_version = ""
        self.current = ""                 # open conversation id
        self.messages: list[dict] = []    # of the open conversation
        self.perm_queue: list[dict] = []
        self.chat_allowed: dict[str, set[str]] = {}
        self.auto_answer = None           # tests: fn(request) -> "once" | "chat" | "deny"
        self.log: list[str] = []
        self._turn_conv = ""
        self._stream: dict | None = None  # the assistant message being streamed
        self._thought: dict | None = None
        self._tools: dict[str, dict] = {}
        self._pending_prompt: tuple[str, list] | None = None
        self._session_conv = ""           # conversation the open grok session belongs to
        self._needs_context: set[str] = set()
        self._active_calls: set[tuple[int, int]] = set()
        self._login_proc: QProcess | None = None
        self.stuck = False
        self.phase = ""            # live activity for the UI: waiting | thinking | writing | working | ""
        self.phase_label = ""
        self._stuck_logged_at = 0.0
        self.watchdog_secs = float(os.environ.get("GD_WATCHDOG_SECS") or self.settings.get("watchdog_secs", 60))
        self.stop_kill_secs = float(os.environ.get("GD_STOP_KILL_SECS") or 8)
        self._watchdog = QTimer(self, interval=max(200, int(min(5.0, self.watchdog_secs / 3) * 1000)), timeout=self._check_stuck)
        self._watchdog.start()
        self._stop_timer = QTimer(self, singleShot=True, timeout=self._stop_expired)
        self._kill_timer = QTimer(self, singleShot=True, interval=1500, timeout=self._hard_kill)
        self._hide_timer = QTimer(self, singleShot=True, interval=1200, timeout=lambda: self.controlActive.emit(False, ""))
        self._save_timer = QTimer(self, singleShot=True, interval=1500, timeout=self._flush_stream)
        a = self.acp
        a.stateChanged.connect(self._on_acp_state)
        a.needsLogin.connect(lambda why: self.notice.emit(why))
        a.sessionStarted.connect(self._on_session)
        a.messageChunk.connect(self._on_chunk)
        a.thoughtChunk.connect(self._on_thought)
        a.toolCallChanged.connect(self._on_tool)
        a.permissionRequested.connect(self._on_acp_permission)
        a.questionRequested.connect(lambda mid, _p: a.respond_question(mid, None))
        a.turnFinished.connect(self._on_turn_finished)
        a.errorOccurred.connect(lambda m: self._note(m, "error"))
        a.logLine.connect(self._log)
        a.agentInfo.connect(self._on_agent_info)
        self.control.beginRequested.connect(self._on_control_begin)
        self.control.callEnded.connect(self._on_control_end)

    # ================================================================ plumbing ==
    def _log(self, line: str) -> None:
        self.log.append(time.strftime("%H:%M:%S ") + line)
        del self.log[:-500]
        try:  # also keep %APPDATA%\GrokDesktop\grok-desktop.log (rotated at 1 MB) for troubleshooting
            lp = os.path.join(paths.data_dir(), "grok-desktop.log")
            if os.path.exists(lp) and os.path.getsize(lp) > 1_000_000:
                os.replace(lp, lp + ".1")
            with open(lp, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line[:2000] + "\n")
        except OSError:
            pass

    def _set_state(self, s: str) -> None:
        if s != self.state:
            self.state = s
            self.stateChanged.emit(s)

    def _on_acp_state(self, s: str) -> None:
        if self.state in ("logging_in", "installing") and s in ("stopped", "needs_login"):
            return
        self._set_state(s)

    @property
    def busy(self) -> bool:
        return self.acp.busy

    def rules(self) -> str:
        import platform
        return RULES.format(pc="Windows PC" if paths.IS_WINDOWS else "Linux computer", shell="powershell" if paths.IS_WINDOWS else "shell",
                            shell_name="PowerShell" if paths.IS_WINDOWS else "the shell (bash)", os=platform.platform(), home=paths.HOME, date=time.strftime("%A %d %B %Y"))

    def mcp_servers(self) -> list[dict]:
        if not self.settings.get("computer_control", True):
            return []
        cmd, args, extra = paths.mcp_server_command()
        env = {"GD_CONTROL_PORT": str(self.control.port), "GD_CONTROL_TOKEN": self.control.token,
               "GD_HOME": paths.data_dir(), **extra}
        if os.environ.get("GD_FAKE_DESKTOP"):
            env["GD_FAKE_DESKTOP"] = os.environ["GD_FAKE_DESKTOP"]
        return [{"name": SERVER_NAME, "command": cmd, "args": args,
                 "env": [{"name": k, "value": v} for k, v in env.items()]}]

    # ================================================================== start ==
    def start(self) -> None:
        self.grok = paths.find_grok(self.settings.get("grok_path", ""))
        if not self.grok:
            self.grok_version = ""
            self._set_state("no_grok")
            return
        self.grok_version = paths.grok_version(self.grok) or ""
        self._log(f"grok at {self.grok}: {self.grok_version}")
        sid = (self.store.get(self.current) or {}).get("session_id", "") if self.current else ""
        self._session_conv = self.current
        self.acp.start(self.grok, paths.workspace_dir(), self.rules(), self.mcp_servers(),
                       self.settings.get("model", ""), resume_id=sid)

    def restart(self) -> None:
        self.acp.stop()
        self.start()

    def shutdown(self) -> None:
        self._flush_stream()
        self._drop_permissions()
        if self._login_proc is not None:
            self._login_proc.kill()
            self._login_proc.waitForFinished(1000)
        self.acp.stop()
        self.control.close()

    # =========================================================== conversations ==
    def conversations(self, query: str = "") -> list[dict]:
        return self.store.list(query)

    def new_chat(self) -> None:
        """Start a fresh conversation (created in the history when the first message is sent)."""
        self._leave_current()
        self.current = ""
        self.messages = []
        self.currentChanged.emit("")
        self.messagesChanged.emit()
        if self.acp.state in ("ready",) and self.acp.session_id and self._session_conv:
            self._session_conv = ""
            self.acp.open_session("")

    def open_chat(self, cid: str) -> None:
        if cid == self.current:
            return
        conv = self.store.get(cid)
        if not conv:
            return
        self._leave_current()
        self.current = cid
        self.messages = self.store.messages(cid)
        self.currentChanged.emit(cid)
        self.messagesChanged.emit()
        if self.acp.state == "ready":
            self._session_conv = cid
            self.acp.open_session(conv.get("session_id", ""))

    def _leave_current(self) -> None:
        if self.acp.busy:
            self.stop()
            self._note("Stopped because you switched chats.")
        self._flush_stream()

    def rename_chat(self, cid: str, title: str) -> None:
        self.store.rename(cid, title)
        self.conversationsChanged.emit()

    def delete_chat(self, cid: str) -> None:
        if cid == self.current:
            self.new_chat()
        self.store.delete(cid)
        shutil.rmtree(os.path.join(paths.attachments_dir(), cid), ignore_errors=True)
        self.chat_allowed.pop(cid, None)
        self.conversationsChanged.emit()

    # =============================================================== sending ==
    def prepare_attachment(self, src: str, cid: str | None = None) -> dict:
        """Copy a file into %APPDATA%\\GrokDesktop\\attachments\\<chat> and describe it."""
        folder = os.path.join(paths.attachments_dir(), cid or "pending")
        os.makedirs(folder, exist_ok=True)
        name = os.path.basename(src)
        dst = os.path.join(folder, f"{int(time.time() * 1000) % 10**9}-{name}")
        shutil.copyfile(src, dst)
        ext = os.path.splitext(name)[1].lower()
        att = {"name": name, "path": dst, "size": os.path.getsize(dst), "kind": "file"}
        if ext in IMAGE_MIME:
            att["kind"] = "image"
            with open(dst, "rb") as f:
                wh = size_of(f.read(65536))
            if wh:
                att["width"], att["height"] = wh
        elif ext in TEXT_EXT or (mimetypes.guess_type(name)[0] or "").startswith("text/"):
            att["kind"] = "text"
        return att

    def save_pasted_image(self, png_bytes: bytes) -> str:
        folder = os.path.join(paths.attachments_dir(), "pending")
        os.makedirs(folder, exist_ok=True)
        p = os.path.join(folder, time.strftime("pasted-%Y%m%d-%H%M%S.png"))
        with open(p, "wb") as f:
            f.write(png_bytes)
        return p

    def _blocks(self, cid: str, text: str, atts: list[dict]) -> list[dict]:
        pre = ""
        if cid in self._needs_context:
            ctx = self.store.context_text(cid)
            if ctx:
                pre = ("[Earlier in this conversation (your previous session couldn't be reopened), for context:]\n"
                       f"{ctx}\n[End of earlier conversation]\n\n")
            self._needs_context.discard(cid)
        blocks: list[dict] = []
        notes = []
        for a in atts:
            uri = pathlib.Path(a["path"]).as_uri()
            if a["kind"] == "image":
                if self.acp.supports_images and a["size"] <= 8_000_000:
                    with open(a["path"], "rb") as f:
                        blocks.append({"type": "image", "mimeType": IMAGE_MIME[os.path.splitext(a["path"])[1].lower()],
                                       "data": base64.b64encode(f.read()).decode(), "uri": uri})
                    notes.append(f"- image {a['name']} (also saved at {a['path']})")
                else:
                    dims = f", {a.get('width')}×{a.get('height')}" if a.get("width") else ""
                    notes.append(f"- image {a['name']}{dims}: saved at {a['path']} — look at it with the "
                                 f"grok-desktop view_image tool")
                continue
            content = None
            if a["kind"] == "text" and a["size"] <= MAX_EMBED:
                try:
                    with open(a["path"], encoding="utf-8") as f:
                        content = f.read()
                except (UnicodeDecodeError, OSError):
                    content = None
            if content is not None and self.acp.supports_embedded:
                blocks.append({"type": "resource", "resource": {"uri": uri, "mimeType": "text/plain", "text": content}})
                notes.append(f"- file {a['name']} (content included; saved at {a['path']})")
            elif content is not None:
                notes.append(f"- file {a['name']} (saved at {a['path']}):\n```\n{content}\n```")
            else:
                notes.append(f"- file {a['name']} ({a['size']:,} bytes): saved at {a['path']} — use the tools to read it")
        body = pre + (text or "(see the attachments)")
        if notes:
            body += "\n\nAttached:\n" + "\n".join(notes)
        return [{"type": "text", "text": body}] + blocks

    def send(self, text: str, attachments: list[str] | None = None, voice: bool = False) -> bool:
        text = (text or "").strip()
        attachments = attachments or []
        if not text and not attachments:
            return False
        if self.acp.busy:
            self.notice.emit("Grok is still answering — press Stop first.")
            return False
        if self.state in ("no_grok", "needs_login", "logging_in", "installing"):
            self.notice.emit("Grok isn't ready: " + {"no_grok": "install it first", "needs_login": "sign in first"}
                             .get(self.state, "wait a moment") + ".")
            return False
        if self.control.killed:  # a new message from the user = computer control may continue
            self.resume_control()
        if not self.current:
            title = " ".join(text.split())[:48] or os.path.basename(attachments[0])
            self.current = self.store.create(title)
            if self.acp.session_id and not self._session_conv:  # the fresh session waiting for a new chat
                self._session_conv = self.current
                self.store.set_session(self.current, self.acp.session_id, self.acp.current_model)
            self.currentChanged.emit(self.current)
            self.conversationsChanged.emit()
        self.turn_voice = voice
        cid = self.current
        atts = [self.prepare_attachment(p, cid) for p in attachments]
        mid = self.store.add(cid, "user", text, atts)
        self.messages.append({"id": mid, "role": "user", "text": text, "attachments": atts, "meta": {}})
        self.messagesChanged.emit()
        self.conversationsChanged.emit()
        if self.acp.state == "ready" and self.acp.session_id and self._session_conv == cid:
            self._prompt(cid, text, atts)
        else:
            self._pending_prompt = (text, atts)
            if self.acp.state == "ready":
                self._session_conv = cid
                self.acp.open_session((self.store.get(cid) or {}).get("session_id", ""))
            elif self.acp.state in ("stopped", "error"):
                self.start()
        return True

    def _prompt(self, cid: str, text: str, atts: list[dict]) -> None:
        self.phase, self.phase_label = "waiting", ""
        self._turn_conv = cid
        self._stream = self._thought = None
        self._tools = {}
        if not self.acp.prompt(self._blocks(cid, text, atts)):
            self._note("Couldn't send that message; try again in a moment.", "error")

    def stop(self) -> None:
        """The Stop button: cancel the running answer. If grok ignores the cancel, restart it (resuming this chat)."""
        self._drop_permissions()
        if self.acp.busy:
            self._log(f"Stop pressed: session/cancel sent (turn #{self.acp._turn_id}); will restart grok if it doesn't answer in {self.stop_kill_secs:g}s")
            self._stop_timer.start(int(self.stop_kill_secs * 1000))
        self.acp.cancel()

    def _stop_expired(self) -> None:
        if self.acp.busy:
            self._log("grok ignored the cancel: restarting the agent")
            self.restart_agent("grok didn't stop when asked, so I restarted it.")

    # ---------------------------------------------------------- watchdog / recovery --
    def _check_stuck(self) -> None:
        if not self.acp.busy or self.perm_queue or self._active_calls:
            if self.stuck:
                self.stuck = False
                self.stuckChanged.emit(False, 0)
            return
        quiet = time.time() - self.acp.last_traffic
        if quiet >= self.watchdog_secs:
            if not self.stuck or time.time() - self._stuck_logged_at > 60:
                self._stuck_logged_at = time.time()
                self._log(f"WATCHDOG: no ACP traffic for {quiet:.0f}s during turn #{self.acp._turn_id}\n" + self.diagnostics())
            if not self.stuck:
                self.stuck = True
                self.stuckChanged.emit(True, int(quiet))
        elif self.stuck:
            self.stuck = False
            self.stuckChanged.emit(False, 0)

    def restart_agent(self, why: str = "") -> None:
        """Kill grok and start it again, resuming this chat's session (history stays)."""
        self._log("restart agent: " + (why or "user request"))
        self._drop_permissions()
        self._stop_timer.stop()
        self._kill_timer.stop()
        was = self.acp.busy
        if self.stuck:
            self.stuck = False
            self.stuckChanged.emit(False, 0)
        self.acp.kill()
        self._pending_prompt = None
        if was or why:
            self._note(why or "Restarted grok. Your chat is still here; send your message again.", "info")
        QTimer.singleShot(200, self.start)

    def diagnostics(self) -> str:
        a, now = self.acp, time.time()
        age = lambda t: f"{now - t:.1f}s ago"  # noqa: E731
        out = [f"Grok Desktop {__version__}  grok {self.grok_version or '?'} ({self.grok or 'not found'})",
               f"time {time.strftime('%Y-%m-%d %H:%M:%S')}  platform {sys.platform}  python {sys.version.split()[0]}",
               f"controller state={self.state} acp state={a.state} busy={a.busy} stuck={self.stuck} mode={self.mode} "
               f"model={a.current_model}",
               f"session={a.session_id} chat={self.current or '-'} turn_chat={self._turn_conv or '-'} turn_id={a._turn_id} "
               f"turn_age={(now - a._turn_t0):.1f}s" if a.busy else
               f"session={a.session_id} chat={self.current or '-'} (no turn running)",
               f"last grok traffic {age(a.last_traffic)}; watchdog {self.watchdog_secs:g}s",
               f"pending ACP requests: {a.pending_summary()}",
               f"permission queue: {len(self.perm_queue)}  active control calls: {len(self._active_calls)}  "
               f"mcp control port open: {self.control is not None}",
               f"messages in open chat: {len(self.messages)}  tool calls tracked: {len(a._tool_calls)}",
               "recent ACP messages ('>' we sent, '<' grok sent):"]
        out += [f"  {time.strftime('%H:%M:%S', time.localtime(t))} {d} {w}" for t, d, w in list(a.trace)[-40:]]
        out.append("recent app log:")
        out += ["  " + l for l in self.log[-60:]]
        return "\n".join(out)

    # ================================================================ session ==
    def _on_session(self, sid: str, how: str) -> None:
        cid = self._session_conv
        if cid != self.current:  # the user switched chats while grok was connecting
            target = self.current
            tsid = (self.store.get(target) or {}).get("session_id", "") if target else ""
            if tsid and tsid != sid:
                self._session_conv = target
                self.acp.open_session(tsid)
                return
            cid = self._session_conv = target
        self._log(f"session {how}: {sid} for chat {cid or '(new)'}")
        if cid:
            self.store.set_session(cid, sid, self.acp.current_model)
            spoken = [m for m in self.store.messages(cid) if m["role"] in ("user", "assistant")]
            if how == "new" and len(spoken) > (1 if self._pending_prompt else 0):
                self._needs_context.add(cid)  # grok lost the old session: resend recent history with the next prompt
        if self._pending_prompt and cid == self.current:
            text, atts = self._pending_prompt
            self._pending_prompt = None
            self._prompt(cid, text, atts)

    # ============================================================== streaming ==
    def _visible(self) -> bool:
        return self._turn_conv == self.current

    def _on_chunk(self, text: str) -> None:
        self.phase, self.phase_label = "writing", ""
        if self._thought is not None:
            self._persist(self._thought)
            self._thought = None
        if self._stream is None:
            self._stream = {"id": None, "role": "assistant", "text": "", "attachments": [], "meta": {}, "streaming": True,
                            "conv": self._turn_conv}
            if self._visible():
                self.messages.append(self._stream)
        self._stream["text"] += text
        self.assistantText.emit(text)
        if not self._save_timer.isActive():
            self._save_timer.start()
        if self._visible():
            self.messagesChanged.emit()

    def _on_thought(self, text: str) -> None:
        self.phase, self.phase_label = "thinking", ""
        if self._thought is None:
            self._thought = {"id": None, "role": "thought", "text": "", "attachments": [], "meta": {}, "conv": self._turn_conv}
            if self._visible():
                self.messages.append(self._thought)
        self._thought["text"] += text
        if self._visible():
            self.messagesChanged.emit()

    def _on_tool(self, tc: dict) -> None:
        name, args = permissions.resolve_tool(tc)
        if "__" in name and name.split("__", 1)[0] == SERVER_NAME:
            a = permissions.assess_mcp(name.split("__", 1)[1], args if isinstance(args, dict) else {})
            label = a["title"] + (f": {a['detail'][:80]}" if a["detail"] else "")
        else:
            label = tc.get("title") or name or "tool"
        tid = tc.get("toolCallId", "")
        self.phase, self.phase_label = "working", label[:70]
        m = self._tools.get(tid)
        if m is None:
            if self._stream is not None:   # text after a tool call starts a new bubble
                self._flush_stream(final=True)
            m = {"id": None, "role": "tool", "text": label, "attachments": [], "meta": {"status": tc.get("status", "")},
                 "conv": self._turn_conv}
            self._tools[tid] = m
            self.toolStep.emit()
            if self._visible():
                self.messages.append(m)
        m["text"] = label
        m["meta"] = {"status": tc.get("status", ""), "toolCallId": tid}
        self._persist(m)
        if self._visible():
            self.messagesChanged.emit()

    def _persist(self, m: dict) -> None:
        cid = m.get("conv") or self._turn_conv
        if not cid or not self.store.get(cid):
            return
        if m.get("id") is None:
            m["id"] = self.store.add(cid, m["role"], m["text"], m.get("attachments"), m.get("meta"))
        else:
            self.store.update(m["id"], m["text"], m.get("meta"))

    def _flush_stream(self, final: bool = False) -> None:
        if self._stream is not None:
            self._persist(self._stream)
            if final:
                self._stream["streaming"] = False
                self._stream = None
        if self._thought is not None:
            self._persist(self._thought)
            if final:
                self._thought = None

    def _on_turn_finished(self, reason: str) -> None:
        self.phase, self.phase_label = "", ""
        self._flush_stream(final=True)
        self._drop_permissions()
        self._kill_timer.stop()
        self._stop_timer.stop()
        if self.stuck:
            self.stuck = False
            self.stuckChanged.emit(False, 0)
        if reason == "cancelled":
            self._note("Stopped.")
        elif reason == "max_tokens":
            self._note("Grok hit its answer length limit.")
        elif reason == "refusal":
            self._note("Grok declined to answer that.")
        if self._turn_conv:
            self.store.touch(self._turn_conv)
        self.conversationsChanged.emit()
        self.messagesChanged.emit()
        self.turnEnded.emit(reason)

    def _note(self, text: str, kind: str = "info") -> None:
        cid = self._turn_conv if self.acp.busy else self.current
        m = {"id": None, "role": "note", "text": text, "attachments": [], "meta": {"kind": kind}, "conv": cid}
        if cid and self.store.get(cid):
            m["id"] = self.store.add(cid, "note", text, meta={"kind": kind})
        if cid == self.current:
            self.messages.append(m)
            self.messagesChanged.emit()
        self.notice.emit(text)

    # ============================================================ permissions ==
    @property
    def mode(self) -> str:
        return permissions.normalize_mode(self.settings.get("permission_mode"))

    def _allowed_set(self, cid: str) -> set[str]:
        return self.chat_allowed.setdefault(cid or "-", set())

    def _on_acp_permission(self, mid, params: dict) -> None:
        tc, options = params.get("toolCall") or {}, params.get("options") or []
        a = permissions.assess(tc, options)
        verdict = permissions.decide(a, self.mode, self._allowed_set(self._turn_conv))
        self._log(f"ACP permission {a['key']} ({a['category']}) mode={self.mode} -> {verdict}")
        req = {"source": "acp", "id": mid, "options": options, "assessment": a, "conv": self._turn_conv}
        if verdict == "allow":
            self._finish_permission(req, "once", auto=True)
        else:
            self._queue(req)

    def _on_control_begin(self, conn: int, call: int, tool: str, args: dict) -> None:
        a = permissions.assess_mcp(tool, args)
        if self.control.consume_grant(tool, args):
            verdict = "allow"
        else:
            verdict = permissions.decide(a, self.mode, self._allowed_set(self._turn_conv))
        self._log(f"tool call {tool} ({a['category']}) -> {verdict}")
        req = {"source": "control", "conn": conn, "call": call, "assessment": a, "conv": self._turn_conv}
        if verdict == "allow":
            self._finish_permission(req, "once", auto=True)
        else:
            self._queue(req)

    def _queue(self, req: dict) -> None:
        self.perm_queue.append(req)
        if self.auto_answer is not None:
            QTimer.singleShot(0, lambda: self.answer_permission(self.auto_answer(req["assessment"]), req))
            return
        if len(self.perm_queue) == 1:
            self.permissionChanged.emit()
            self.wantShow.emit()

    def current_permission(self) -> dict | None:
        return self.perm_queue[0] if self.perm_queue else None

    def answer_permission(self, choice: str, req: dict | None = None) -> None:
        """choice: once | chat | deny"""
        req = req or self.current_permission()
        if req is None or req not in self.perm_queue:
            return
        self.perm_queue.remove(req)
        self._finish_permission(req, choice)
        self.permissionChanged.emit()

    def _finish_permission(self, req: dict, choice: str, auto: bool = False) -> None:
        a = req["assessment"]
        allow = choice in ("once", "chat")
        if choice == "chat" and a.get("allow_chat_ok"):
            self._allowed_set(req.get("conv", "")).add(a["key"])
        if req["source"] == "acp":
            if allow and a.get("server") == SERVER_NAME:
                self.control.grant(a["tool"], a.get("args"))
            self.acp.respond_permission(req["id"], permissions.pick_option(req["options"], "allow" if allow else "deny"))
        else:
            if allow and a["tool"] in INPUT_TOOLS:
                self._active_calls.add((req["conn"], req["call"]))
                self._hide_timer.stop()
                self.controlActive.emit(True, f"{a['title']} {a['detail']}".strip())
            self.control.reply(req["conn"], req["call"], allow, "" if allow else "The user said no.")
        if not auto:
            verb = {"once": "Allowed", "chat": "Allowed for this chat", "deny": "Denied"}.get(choice, "Denied")
            self._note(f"{verb}: {a['title']}" + (f" — {a['detail'][:100]}" if a["detail"] else ""), "permission")
        elif a["risk"] == "high" and self.mode == "all":
            self._note(f"Allowed automatically (Allow everything): {a['title']}", "permission")

    def _on_control_end(self, conn: int, call: int, ok: bool, message: str) -> None:
        if (conn, call) in self._active_calls:
            self._active_calls.discard((conn, call))
            if not self._active_calls:
                self._hide_timer.start()

    def _drop_permissions(self) -> None:
        while self.perm_queue:
            req = self.perm_queue.pop(0)
            if req["source"] == "acp":
                self.acp.respond_permission(req["id"], {"outcome": "cancelled"})
            else:
                self.control.reply(req["conn"], req["call"], False, "Cancelled.")
        self.permissionChanged.emit()

    def set_mode(self, mode: str) -> bool:
        """'all' is refused until the one-time warning was accepted (acknowledge_allow_all)."""
        if mode not in permissions.MODES:
            return False
        if mode == "all" and not self.settings.get("allow_all_warned"):
            return False
        self.settings.set("permission_mode", mode)
        return True

    def acknowledge_allow_all(self) -> None:
        self.settings.set("allow_all_warned", True)

    # ========================================================= emergency stop ==
    def emergency_stop(self) -> None:
        """Ctrl+Alt+Esc / the Stop button on the indicator: halt all computer control right now."""
        self._log("EMERGENCY STOP")
        self.control.abort_all()
        self._active_calls.clear()
        self._hide_timer.stop()
        self.controlActive.emit(False, "")
        self.killedChanged.emit(True)
        if self.acp.busy:
            self._drop_permissions()
            self.acp.cancel()
            self._kill_timer.start()   # grok didn't stop in time -> kill it
        self._note("Emergency stop: computer control halted. Send a new message (or press Resume) to continue.", "error")

    def _hard_kill(self) -> None:
        if self.acp.busy:
            self._log("grok didn't stop after cancel: killing it")
            self.acp.kill()
            QTimer.singleShot(300, self.start)

    def resume_control(self) -> None:
        self.control.resume()
        self.killedChanged.emit(False)

    # ================================================================= models ==
    def _on_agent_info(self, info: dict) -> None:
        self._log(f"agent: version={info.get('version')} model={info.get('model')} images={info.get('image')} "
                  f"models={[m.get('modelId') for m in info.get('models') or []]}")
        self.modelsChanged.emit()

    def models(self) -> list[dict]:
        return self.acp.models

    def set_model(self, model_id: str) -> None:
        self.settings.set("model", model_id)
        if not self.acp.session_id:
            return

        def done(ok: bool):
            if ok:
                self.notice.emit(f"Model: {model_id}")
            elif not self.acp.busy:  # older grok without session/set_model: restart with --model, keep the chat
                self.notice.emit(f"Switching to {model_id}…")
                self.restart()
            self.modelsChanged.emit()
        self.acp.set_model(model_id, done)

    # ========================================================== install/login ==
    def install_grok(self) -> None:
        """Open a PowerShell window running xAI's official per-user installer."""
        if paths.IS_WINDOWS:
            QProcess.startDetached("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command",
                                                      "irm https://x.ai/cli/install.ps1 | iex; "
                                                      "Write-Host 'Done - go back to Grok Desktop and press Retry.'"])
        else:
            cmd = paths.LINUX_INSTALL + "; echo; read -p 'Done - go back to Grok Desktop and press Retry. (Enter to close)'"
            for term, flag in (("x-terminal-emulator", "-e"), ("gnome-terminal", "--"), ("zorin-terminal", "--"),
                               ("konsole", "-e"), ("xfce4-terminal", "-x"), ("kitty", ""), ("alacritty", "-e"),
                               ("xterm", "-e")):
                exe = shutil.which(term)
                if exe:
                    QProcess.startDetached(exe, ([flag] if flag else []) + ["bash", "-c", cmd])
                    break
            else:
                self.notice.emit("No terminal found. Run this in a terminal, then press Retry:  " + paths.LINUX_INSTALL)
                return
        self.notice.emit("The installer opened in a new window. When it says it's done, press Retry.")

    def start_login(self, device_code: bool = False) -> None:
        if not self.grok or self._login_proc is not None:
            return
        self.acp.stop()
        self._set_state("logging_in")
        p = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("NO_COLOR", "1")
        p.setProcessEnvironment(env)
        p.setProcessChannelMode(QProcess.MergedChannels)
        buf = {"text": ""}

        def out():
            buf["text"] += bytes(p.readAll()).decode("utf-8", "replace")
            info = paths.parse_login_output(buf["text"])
            if info["url"] or info["code"]:
                self.loginInfo.emit(info["url"], info["code"])

        def done(code, _st):
            out()
            self._log("grok login: " + paths.strip_ansi(buf["text"])[-1500:])
            self._login_proc = None
            self.loginInfo.emit("", "")
            self._set_state("stopped")
            if code == 0:
                self.notice.emit("Signed in!")
            else:
                self.notice.emit("Sign-in didn't finish. Try again (or use a code).")
            self.start()

        p.readyRead.connect(out)
        p.finished.connect(done)
        self._login_proc = p
        p.start(self.grok, ["login", "--device-auth"] if device_code else ["login"])

    def cancel_login(self) -> None:
        if self._login_proc is not None:
            self._login_proc.kill()
