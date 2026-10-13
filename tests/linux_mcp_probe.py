"""Probe the real `python -m grok_desktop --mcp-server` (or an installed `grok-desktop --mcp-server`) over stdio on a live
display, with a stub control server that allows every call. Usage: linux_mcp_probe.py [evidence.json]"""
import json, os, shutil, socket, subprocess, sys, threading
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
port, token = srv.getsockname()[1], "probe"
def serve():
    c, _ = srv.accept()
    for line in c.makefile("rb"):
        m = json.loads(line)
        if m.get("op") == "begin":
            c.sendall((json.dumps({"op": "decision", "id": m["id"], "allow": True}) + "\n").encode())
threading.Thread(target=serve, daemon=True).start()
env = {**os.environ, "GD_CONTROL_PORT": str(port), "GD_CONTROL_TOKEN": token, "GD_HOME": "/tmp/gd-probe"}
for k in ("GD_FAKE_DESKTOP", "GD_PLATFORM"):
    env.pop(k, None)
cmd = [shutil.which("grok-desktop"), "--mcp-server"] if os.environ.get("GD_PROBE_INSTALLED") else \
    [sys.executable, "-m", "grok_desktop", "--mcp-server"]
if not os.environ.get("GD_PROBE_INSTALLED"):
    env["PYTHONPATH"] = os.path.join(ROOT, "src")
p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
n = 0
def rpc(method, params=None):
    global n
    n += 1
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n, "method": method, "params": params or {}}) + "\n"); p.stdin.flush()
    return json.loads(p.stdout.readline())["result"]
rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "probe", "version": "0"}})
names = [t["name"] for t in rpc("tools/list")["tools"]]
print("tools:", len(names), names)
ev = []
for tool, args in [("shell", {"command": "echo hello from $(uname -s)"}), ("screen_info", {}), ("screenshot", {"max_width": 640}),
                   ("list_windows", {}), ("mouse_move", {"x": 100, "y": 100}), ("keyboard_hotkey", {"keys": "shift"}),
                   ("fs_list", {"path": "~"}), ("open_url", {"url": "https://example.com"}),
                   ("open_app", {"name": "nonexistent-app-xyz"})]:
    r = rpc("tools/call", {"name": tool, "arguments": args})
    kinds = [c["type"] for c in r["content"]]
    txt = next((c["text"] for c in r["content"] if c["type"] == "text"), "")[:140].replace("\n", " ")
    print(f"{tool:16} isError={r.get('isError', False)!s:5} {kinds} {txt}")
    ev.append({"tool": tool, "isError": r.get("isError", False), "content_types": kinds, "text": txt})
p.stdin.close(); p.wait(5)
if len(sys.argv) > 1:
    json.dump(ev, open(sys.argv[1], "w"), indent=1)
