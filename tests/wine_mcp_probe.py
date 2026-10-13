"""Drive the *built* GrokDesktop.exe --mcp-server under wine with a stub "app" that allows every call, and
exercise the real Windows desktop code (ctypes SendInput / GDI / EnumWindows) against wine's desktop.

    python3 tests/wine_mcp_probe.py <path to GrokDesktop.exe> <evidence dir>
"""
import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time

exe, out = sys.argv[1], os.path.abspath(sys.argv[2])
os.makedirs(out, exist_ok=True)
log = []
STAMP = time.strftime("%H%M%S")

# --- stub control server (what the app does: check token, decide; here: always allow) ---
srv = socket.socket()
srv.bind(("127.0.0.1", 0))
srv.listen(1)
port, token = srv.getsockname()[1], "probe-token"
begins = []


def serve():
    c, _ = srv.accept()
    f = c.makefile("rb")
    for line in f:
        m = json.loads(line)
        if m.get("op") == "hello":
            assert m["token"] == token
        elif m.get("op") == "begin":
            begins.append(m["tool"])
            c.sendall((json.dumps({"op": "decision", "id": m["id"], "allow": True}) + "\n").encode())


threading.Thread(target=serve, daemon=True).start()
env = dict(os.environ, GD_CONTROL_PORT=str(port), GD_CONTROL_TOKEN=token, WINEDEBUG="-all")
env.pop("GD_FAKE_DESKTOP", None)
p = subprocess.Popen(["wine", exe, "--mcp-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
nid = [0]


def rpc(method, params=None, timeout=60):
    nid[0] += 1
    p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": nid[0], "method": method, "params": params or {}}) + "\n").encode())
    p.stdin.flush()
    t0 = time.time()
    while time.time() - t0 < timeout:
        line = p.stdout.readline()
        if not line:
            raise SystemExit("server closed stdout")
        m = json.loads(line)
        if m.get("id") == nid[0]:
            return m
    raise SystemExit(f"timeout on {method}")


def tool(_tool, **args):
    r = rpc("tools/call", {"name": _tool, "arguments": args})["result"]
    texts = [c.get("text", "") for c in r["content"] if c["type"] == "text"]
    imgs = [c for c in r["content"] if c["type"] == "image"]
    log.append({"tool": _tool, "args": args, "isError": r.get("isError", False), "text": " | ".join(texts)[:600],
                "images": len(imgs)})
    print(f"{_tool}: error={r.get('isError', False)} images={len(imgs)} {(' | '.join(texts))[:200]!r}")
    return r, texts, imgs


init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "probe"}})
print("initialize:", init["result"]["serverInfo"])
p.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
names = [t["name"] for t in rpc("tools/list")["result"]["tools"]]
print(len(names), "tools:", names)
tool("screen_info")
tool("fs_write", path=f"~/gd-probe-{STAMP}.txt", content="hello from the wine probe\n")
tool("fs_read", path=f"~/gd-probe-{STAMP}.txt")
tool("fs_list", path="~")
tool("fs_search", root="~", name="gd-probe*")
tool("powershell", command="Get-Date")      # wine has no real PowerShell; expected to fail cleanly
tool("open_app", name="notepad")
time.sleep(4)
tool("list_windows")
tool("focus_window", window="Notepad")
time.sleep(1)
tool("keyboard_type", text="Typed by Grok Desktop via SendInput under wine")
tool("keyboard_hotkey", keys="enter")
tool("mouse_move", x=300, y=300)
tool("mouse_click", x=320, y=320)
tool("mouse_scroll", amount=-2)
time.sleep(1)
r, texts, imgs = tool("screenshot")
if imgs:
    with open(os.path.join(out, "wine-mcp-screenshot.png"), "wb") as f:
        f.write(base64.b64decode(imgs[0]["data"]))
r, texts, imgs = tool("view_image", path=os.path.join(out, "wine-mcp-screenshot.png").replace("/", "\\").join(["Z:", ""]))
tool("fs_move", source=f"~/gd-probe-{STAMP}.txt", destination=f"~/gd-probe-moved-{STAMP}.txt")
tool("fs_delete", path=f"~/gd-probe-moved-{STAMP}.txt")
p.stdin.close()
p.wait(20)
json.dump({"tools": names, "allowed_begins": begins, "calls": log}, open(os.path.join(out, "wine-mcp-probe.json"), "w"), indent=1)
print("PROBE_DONE", len(begins), "control-link begins")
