"""MCP (Model Context Protocol) server over stdio, no dependencies (adapted from gridos-companion's
gridos_control_mcp/server.py). grok starts it as `GrokDesktop.exe --mcp-server` (or `python -m grok_desktop
--mcp-server`) and talks JSON-RPC 2.0, one JSON object per line. Logs go to stderr."""
from __future__ import annotations

import io
import json
import os
import sys
import time

from .. import __version__
from . import SERVER_NAME, catalog, tools
from .desktop import make_desktop
from .link import BaseLink, make_link

SUPPORTED_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
_INSTR = (
    "Tools to use this {pc} for the user: files (fs_*), {sh}, screenshots, mouse/keyboard, windows and "
    "apps. Workflow for GUI tasks: screenshot -> decide -> mouse/keyboard tool -> screenshot again to check. "
    "Mouse coordinates are in the pixel space of your latest screenshot. Prefer files/{sh} over clicking "
    "when possible. The user approves risky actions in the Grok Desktop app and can stop you at any time "
    "(emergency stop); if a tool says it was stopped or denied, don't retry it on your own.")


def instructions() -> str:
    win = catalog.plat() == "windows"
    return _INSTR.format(pc="Windows PC" if win else "Linux computer", sh="PowerShell" if win else "shell (bash)")


class Server:
    def __init__(self, ctx: tools.Ctx | None = None, out=None, log=None):
        self.out = out or sys.stdout
        self.log = log or sys.stderr
        if ctx is None:
            from .. import paths
            ctx = tools.Ctx(make_desktop(), make_link(), os.path.join(paths.data_dir(), "screenshots"))
        self.ctx = ctx

    @property
    def link(self) -> BaseLink:
        return self.ctx.link

    def _send(self, obj: dict) -> None:
        self.out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.out.flush()

    def _log(self, msg: str) -> None:
        try:
            print(f"[{SERVER_NAME} {time.strftime('%H:%M:%S')}] {msg}", file=self.log, flush=True)
        except (OSError, ValueError, AttributeError):
            pass

    def call_tool(self, name: str, args: dict) -> dict:
        if name not in catalog.TOOLS or name == ("shell" if catalog.plat() == "windows" else "powershell"):
            return {"ok": False, "message": f"Unknown tool '{name}'."}
        info = catalog.classify(name, args)
        allow, reason, cid = self.link.begin(name, args, info)
        if not allow:
            self._log(f"  denied: {reason}")
            return {"ok": False, "message": f"Not allowed: {reason}", "denied": True}
        res = tools.call(self.ctx, name, args)
        self.link.end(cid, bool(res.get("ok")), res.get("message", ""))
        return res

    def handle(self, msg: dict) -> dict | None:
        method, mid = msg.get("method"), msg.get("id")
        is_request = "id" in msg
        params = msg.get("params") or {}
        if method is None:
            return None
        try:
            if method == "initialize":
                want = params.get("protocolVersion")
                result = {"protocolVersion": want if want in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": SERVER_NAME, "title": "Grok Desktop computer control",
                                         "version": __version__},
                          "instructions": instructions()}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": catalog.descriptors()}
            elif method == "tools/call":
                name, args = params.get("name", ""), params.get("arguments") or {}
                self._log(f"call {name} {json.dumps(args, ensure_ascii=False)[:300]}")
                res = self.call_tool(name, args)
                img = res.pop("_image", None)
                self._log(f"  -> ok={res.get('ok')} {str(res.get('message', ''))[:200]}")
                content = [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=1)}]
                if img:
                    content.append({"type": "image", "data": img[0], "mimeType": img[1]})
                result = {"content": content, "structuredContent": res, "isError": not res.get("ok", False)}
            elif method.startswith("notifications/"):
                return None
            else:
                if not is_request:
                    return None
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        except Exception as e:  # noqa: BLE001 - never crash on one bad call
            self._log(f"error in {method}: {e!r}")
            if not is_request:
                return None
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"Internal error: {e}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result} if is_request else None

    def serve(self, inp=None) -> None:
        inp = inp or sys.stdin
        self._log(f"started v{__version__} (desktop={self.ctx.desktop.name}, link={type(self.link).__name__})")
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self._send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            for m in msg if isinstance(msg, list) else [msg]:
                resp = self.handle(m) if isinstance(m, dict) else None
                if resp is not None:
                    self._send(resp)


def _std_streams():
    """A windowed (no-console) PyInstaller exe may start with sys.stdin/stdout = None even though grok gave us
    pipes: reopen them from the OS handles."""
    if sys.stdin is not None and sys.stdout is not None:
        try:
            sys.stdin.reconfigure(encoding="utf-8")
            sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        except (AttributeError, ValueError):
            pass
        return sys.stdin, sys.stdout, sys.stderr
    import ctypes
    import msvcrt
    k32 = ctypes.windll.kernel32
    k32.GetStdHandle.restype = ctypes.c_void_p

    def fd(n, mode):
        h = k32.GetStdHandle(n)
        if not h or h == ctypes.c_void_p(-1).value:
            return None
        return io.open(msvcrt.open_osfhandle(h, os.O_BINARY | (os.O_RDONLY if "r" in mode else 0)), mode, buffering=1,
                       encoding="utf-8", newline="\n" if "w" in mode else None)

    return fd(-10, "r"), fd(-11, "w"), fd(-12, "w") or io.StringIO()


def main() -> int:
    if "--list-tools" in sys.argv:
        for t in catalog.descriptors():
            print(f"{t['name']:16} {t['description'][:90]}")
        return 0
    inp, out, err = _std_streams()
    if inp is None or out is None:
        return 2
    Server(out=out, log=err).serve(inp)
    return 0
