"""The MCP server's line to the Grok Desktop app (localhost TCP + secret token, JSON lines).

Before every tool call the server asks the app: {"op":"begin","id","tool","args","info"} and waits for
{"op":"decision","id","allow","reason"}. The app applies the permission mode, asks the user if needed,
shows the on-screen indicator for mouse/keyboard tools, and enforces the emergency stop. The app can send
{"op":"abort"} at any time: running mouse/keyboard loops stop at the next step. If the app isn't reachable,
every tool is refused (grok can't use these tools behind the app's back).
"""
from __future__ import annotations

import json
import os
import socket
import threading


class Aborted(Exception):
    pass


class BaseLink:
    def __init__(self):
        self.aborted = threading.Event()

    def begin(self, tool: str, args: dict, info: dict) -> tuple[bool, str, int]:
        raise NotImplementedError

    def end(self, call_id: int, ok: bool, message: str = "") -> None:
        pass

    def check(self) -> None:
        if self.aborted.is_set():
            raise Aborted()


class NoLink(BaseLink):
    def __init__(self, why: str = "Grok Desktop isn't running (or this tool server wasn't started by it)."):
        super().__init__()
        self.why = why

    def begin(self, tool, args, info):
        return False, self.why, 0


class AllowLink(BaseLink):
    """Tests only: allow everything."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, dict]] = []

    def begin(self, tool, args, info):
        self.calls.append((tool, args))
        return True, "", len(self.calls)


class SocketLink(BaseLink):
    def __init__(self, port: int, token: str, timeout: float = 900):
        super().__init__()
        self.timeout = timeout
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.sock.settimeout(None)
        self._wlock = threading.Lock()
        self._waiters: dict[int, list] = {}
        self._next = 1
        self.connected = True
        self._send({"op": "hello", "token": token, "pid": os.getpid()})
        threading.Thread(target=self._reader, daemon=True).start()

    def _send(self, obj: dict) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._wlock:
            self.sock.sendall(data)

    def _reader(self) -> None:
        f = self.sock.makefile("rb")
        try:
            for line in f:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                op = msg.get("op")
                if op == "abort":
                    self.aborted.set()
                elif op == "resume":
                    self.aborted.clear()
                elif op == "decision":
                    w = self._waiters.get(msg.get("id"))
                    if w:
                        w[1] = msg
                        w[0].set()
        except OSError:
            pass
        self.connected = False
        self.aborted.set()  # app went away: stop everything
        for w in list(self._waiters.values()):
            w[0].set()

    def begin(self, tool, args, info):
        if not self.connected:
            return False, "Grok Desktop was closed.", 0
        cid = self._next
        self._next += 1
        w = [threading.Event(), None]
        self._waiters[cid] = w
        try:
            self._send({"op": "begin", "id": cid, "tool": tool, "args": args, "info": info})
            if not w[0].wait(self.timeout) or not w[1]:
                return False, "No answer from Grok Desktop (timed out or closed).", cid
        except OSError:
            return False, "Lost the connection to Grok Desktop.", cid
        finally:
            self._waiters.pop(cid, None)
        if w[1].get("allow"):
            if w[1].get("clear_abort"):
                self.aborted.clear()
            return True, "", cid
        return False, w[1].get("reason") or "The user said no.", cid

    def end(self, call_id, ok, message=""):
        try:
            self._send({"op": "end", "id": call_id, "ok": ok, "message": message[:300]})
        except OSError:
            pass


def make_link() -> BaseLink:
    port, token = os.environ.get("GD_CONTROL_PORT"), os.environ.get("GD_CONTROL_TOKEN")
    if not port or not token:
        return NoLink()
    try:
        return SocketLink(int(port), token)
    except (OSError, ValueError) as e:
        return NoLink(f"Can't reach Grok Desktop ({e}).")
