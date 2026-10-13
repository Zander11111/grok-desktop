"""App side of the control link with the grok-desktop MCP server (see mcp/link.py).

* localhost-only QTcpServer on a random port; a connection must say hello with the secret token first.
* begin requests -> beginRequested(conn_id, call_id, tool, args) -> the controller answers with reply().
* abort_all(): emergency stop, tells every connected tool server to stop and refuses new calls until resume().
* grants: when The user approves a grok-desktop tool through grok's own ACP permission prompt (or it was
  auto-allowed), a one-time grant is recorded so the MCP call that follows doesn't ask a second time.
"""
from __future__ import annotations

import json
import secrets
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

from .permissions import canon_args

GRANT_TTL = 180.0


class ControlServer(QObject):
    beginRequested = Signal(int, int, str, dict)   # conn, call id, tool, args
    callEnded = Signal(int, int, bool, str)        # conn, call id, ok, message
    connectionsChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.token = secrets.token_hex(16)
        self.server = QTcpServer(self)
        self.server.newConnection.connect(self._on_new)
        self.conns: dict[int, QTcpSocket] = {}
        self.authed: set[int] = set()
        self._bufs: dict[int, bytes] = {}
        self._next = 1
        self.killed = False
        self.grants: list[tuple[str, str | None, float]] = []   # (tool, canon args | None = any args, time)
        self.pending: set[tuple[int, int]] = set()

    def listen(self) -> int:
        if not self.server.isListening():
            self.server.listen(QHostAddress.LocalHost, 0)
        return self.server.serverPort()

    @property
    def port(self) -> int:
        return self.server.serverPort()

    def close(self) -> None:
        for s in list(self.conns.values()):
            s.abort()
        self.server.close()

    # ---------------------------------------------------------------- sockets --
    def _on_new(self) -> None:
        while self.server.hasPendingConnections():
            s = self.server.nextPendingConnection()
            cid = self._next
            self._next += 1
            self.conns[cid] = s
            self._bufs[cid] = b""
            s.readyRead.connect(lambda c=cid: self._on_read(c))
            s.disconnected.connect(lambda c=cid: self._on_gone(c))

    def _on_gone(self, cid: int) -> None:
        s = self.conns.pop(cid, None)
        self.authed.discard(cid)
        self._bufs.pop(cid, None)
        self.pending = {p for p in self.pending if p[0] != cid}
        if s is not None:
            s.deleteLater()
        self.connectionsChanged.emit(len(self.authed))

    def _send(self, cid: int, obj: dict) -> None:
        s = self.conns.get(cid)
        if s is not None:
            s.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            s.flush()

    def _on_read(self, cid: int) -> None:
        s = self.conns.get(cid)
        if s is None:
            return
        self._bufs[cid] += bytes(s.readAll())
        while b"\n" in self._bufs.get(cid, b""):
            line, self._bufs[cid] = self._bufs[cid].split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            op = msg.get("op")
            if cid not in self.authed:
                if op == "hello" and secrets.compare_digest(str(msg.get("token", "")), self.token):
                    self.authed.add(cid)
                    self.connectionsChanged.emit(len(self.authed))
                    if self.killed:
                        self._send(cid, {"op": "abort"})
                else:
                    s.abort()
                    return
                continue
            if op == "begin":
                call = int(msg.get("id", 0))
                if self.killed:
                    self._send(cid, {"op": "decision", "id": call, "allow": False,
                                     "reason": "The emergency stop is on: computer control is halted."})
                    continue
                self.pending.add((cid, call))
                self.beginRequested.emit(cid, call, str(msg.get("tool", "")), msg.get("args") or {})
            elif op == "end":
                self.callEnded.emit(cid, int(msg.get("id", 0)), bool(msg.get("ok")), str(msg.get("message", "")))

    # ------------------------------------------------------------- decisions --
    def reply(self, cid: int, call: int, allow: bool, reason: str = "") -> None:
        if (cid, call) not in self.pending:
            return
        self.pending.discard((cid, call))
        if allow and self.killed:
            allow, reason = False, "The emergency stop is on: computer control is halted."
        self._send(cid, {"op": "decision", "id": call, "allow": allow, "reason": reason, "clear_abort": allow})

    def abort_all(self) -> None:
        self.killed = True
        self.grants.clear()
        for cid in list(self.authed):
            self._send(cid, {"op": "abort"})
        for cid, call in list(self.pending):
            self.reply(cid, call, False, "Stopped by the user (emergency stop).")

    def resume(self) -> None:
        self.killed = False
        for cid in list(self.authed):
            self._send(cid, {"op": "resume"})

    # ----------------------------------------------------------------- grants --
    def grant(self, tool: str, args: dict | None) -> None:
        now = time.time()
        self.grants = [g for g in self.grants if now - g[2] < GRANT_TTL]
        self.grants.append((tool, canon_args(args) if args else None, now))

    def consume_grant(self, tool: str, args: dict) -> bool:
        now = time.time()
        key = canon_args(args)
        for i, (t, a, ts) in enumerate(self.grants):
            if t == tool and (a is None or a == key) and now - ts < GRANT_TTL:
                del self.grants[i]
                return True
        return False
