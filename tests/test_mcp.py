"""The grok-desktop MCP server: tools on a real temp filesystem + fake desktop, the protocol, the control link."""
import io
import json
import os
import socket
import threading

import pytest

from grok_desktop.mcp import catalog, desktop, link, png, server, tools


@pytest.fixture
def ctx(tmp_path):
    return tools.Ctx(desktop.FakeDesktop(str(tmp_path / "desk")), link.AllowLink(), str(tmp_path / "shots"))


def acts(tmp_path):
    p = tmp_path / "desk" / "actions.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_fs_tools(ctx, tmp_path):
    d = tmp_path / "work"
    r = tools.fs_write(ctx, str(d / "a.txt"), "hello\nworld")
    assert r["ok"] and (d / "a.txt").read_text() == "hello\nworld"
    assert not tools.fs_write(ctx, str(d / "a.txt"), "x")["ok"]            # no silent overwrite
    assert tools.fs_write(ctx, str(d / "a.txt"), "!", append=True)["ok"]
    assert tools.fs_read(ctx, str(d / "a.txt"))["content"] == "hello\nworld!"
    assert tools.fs_read(ctx, str(d / "a.txt"), offset=6, max_chars=3)["content"] == "wor"
    (d / "sub").mkdir()
    (d / "bin.dat").write_bytes(b"\x00\x01binary")
    assert not tools.fs_read(ctx, str(d / "bin.dat"))["ok"]
    lst = tools.fs_list(ctx, str(d))
    assert [i["name"] for i in lst["items"]] == ["sub", "a.txt", "bin.dat"]
    assert [i["name"] for i in tools.fs_list(ctx, str(d), pattern="*.txt")["items"]] == ["a.txt"]
    assert tools.fs_move(ctx, str(d / "a.txt"), str(d / "sub"))["ok"] and (d / "sub" / "a.txt").exists()
    (d / "b.txt").write_text("b")
    assert not tools.fs_move(ctx, str(d / "b.txt"), str(d / "sub" / "a.txt"))["ok"]
    assert tools.fs_move(ctx, str(d / "b.txt"), str(d / "sub" / "a.txt"), overwrite=True)["ok"]
    found = tools.fs_search(ctx, str(d), name="*.txt", contains="B")
    assert found["files"] == [str(d / "sub" / "a.txt")]
    assert tools.fs_delete(ctx, str(d / "sub" / "a.txt"))["ok"]              # -> (fake) Recycle Bin
    assert not (d / "sub" / "a.txt").exists() and os.listdir(tmp_path / "desk" / "RecycleBin")
    assert tools.fs_delete(ctx, str(d / "sub"), permanent=True)["ok"] and not (d / "sub").exists()
    assert not tools.fs_delete(ctx, os.path.expanduser("~"))["ok"]          # never the whole home folder
    assert not tools.fs_delete(ctx, "/")["ok"]


def test_screenshot_maps_coordinates(ctx, tmp_path):
    r = tools.screenshot(ctx, max_width=960)
    assert r["ok"] and (r["width"], r["height"]) == (960, 540) and r["_image"][1] == "image/png"
    assert os.path.exists(r["saved_to"])
    tools.mouse_click(ctx, 480, 270)             # middle of the image = middle of the 1920x1080 screen
    assert ctx.desktop.cursor() == (960, 540)
    r2 = tools.screenshot(ctx, monitor=1, max_width=640)  # second monitor at x=1920
    assert r2["width"] == 640
    tools.mouse_move(ctx, 0, 0)
    assert ctx.desktop.cursor() == (1920, 0)
    w = tools.screenshot(ctx, target="window", window="notepad")
    assert w["ok"] and "Untitled - Notepad" in w["message"]
    assert not tools.screenshot(ctx, monitor=5)["ok"]


def test_input_tools(ctx, tmp_path):
    assert tools.keyboard_type(ctx, "héllo 👋\nnext")["ok"]
    assert tools.keyboard_hotkey(ctx, "ctrl+shift+s")["ok"]
    assert tools.mouse_drag(ctx, 0, 0, 100, 100)["ok"]
    assert tools.mouse_scroll(ctx, -3)["ok"]
    a = acts(tmp_path)
    typed = "".join(x["text"] for x in a if x["op"] == "type")
    assert typed == "héllo 👋\nnext"
    keys = [x for x in a if x["op"] == "keys"]
    assert keys[0] == {**keys[0], "keys": ["ctrl", "shift", "s"], "down": True} and keys[1]["down"] is False
    btn = [x["down"] for x in a if x["op"] == "button"]
    assert btn == [True, False]
    assert sum(1 for x in a if x["op"] == "scroll") == 3


def test_abort_stops_input_and_releases(ctx, tmp_path):
    class StopAfter(link.AllowLink):
        n = 0

        def check(self):
            self.n += 1
            if self.n == 5:  # the user hits the emergency stop in the middle of the drag
                self.aborted.set()
            super().check()
    ctx.link = StopAfter()
    r = tools.call(ctx, "mouse_drag", {"x1": 0, "y1": 0, "x2": 10, "y2": 10})
    assert not r["ok"] and "emergency stop" in r["message"]
    a = acts(tmp_path)
    assert [x["down"] for x in a if x["op"] == "button"] == [True, False]   # pressed, then released at once
    assert sum(1 for x in a if x["op"] == "move") < 10                      # the drag didn't finish
    assert a[-1]["op"] == "release_all"
    ctx.link.aborted.set()
    assert not tools.call(ctx, "keyboard_type", {"text": "x"})["ok"]          # nothing runs while stopped
    assert not any(x["op"] == "type" for x in acts(tmp_path))


def test_windows_and_apps(ctx, tmp_path):
    assert len(tools.list_windows(ctx)["windows"]) == 2
    assert tools.focus_window(ctx, "steam")["ok"]
    assert not tools.focus_window(ctx, "photoshop")["ok"]
    assert tools.open_url(ctx, "x.ai")["message"] == "Opened https://x.ai in the default browser."
    assert not tools.open_url(ctx, "file:///C:/Windows/System32/cmd.exe")["ok"]
    assert tools.open_app(ctx, "spot")["app"] == "Spotify"
    assert not tools.open_app(ctx, "definitely-not-an-app-xyz")["ok"]
    opens = [x["target"] for x in acts(tmp_path) if x["op"] == "open"]
    assert opens == ["https://x.ai", r"C:\fake\Spotify.lnk"]


def test_view_image_and_info(ctx, tmp_path):
    p = tmp_path / "pic.png"
    p.write_bytes(png.encode_rgb(5, 4, b"\x01\x02\x03" * 20))
    r = tools.view_image(ctx, str(p))
    assert r["ok"] and "(5×4)" in r["message"] and r["_image"][1] == "image/png"
    assert tools.fs_read(ctx, str(p))["_image"]  # reading an image returns the picture
    info = tools.screen_info(ctx)
    assert len(info["monitors"]) == 2 and info["foreground_window"] == "Untitled - Notepad"


def test_powershell_tool_runs(ctx):
    r = tools.powershell(ctx, "echo grok")
    assert r["ok"] and r["stdout"].strip() == "grok"
    assert not tools.powershell(ctx, "exit 3")["ok"]


def test_protocol_and_image_content(ctx):
    out = io.StringIO()
    s = server.Server(ctx, out=out, log=io.StringIO())
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "screenshot", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 5, "method": "bogus"}]
    s.serve(io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\nnot json\n"))
    res = [json.loads(l) for l in out.getvalue().splitlines()]
    assert res[0]["result"]["protocolVersion"] == "2025-03-26"
    names = [t["name"] for t in res[1]["result"]["tools"]]
    assert set(names) == set(catalog.TOOLS) - {"shell"} and len(names) == 20
    content = res[2]["result"]["content"]
    assert content[1]["type"] == "image" and content[1]["mimeType"] == "image/png"
    assert "_image" not in res[2]["result"]["structuredContent"]
    assert res[3]["result"]["isError"] is True
    assert res[4]["error"]["code"] == -32601 and res[5]["error"]["code"] == -32700


def test_no_app_means_no_tools(tmp_path):
    ctx = tools.Ctx(desktop.FakeDesktop(str(tmp_path / "d")), link.NoLink(), None)
    s = server.Server(ctx, out=io.StringIO(), log=io.StringIO())
    r = s.call_tool("fs_list", {"path": str(tmp_path)})
    assert not r["ok"] and "isn't running" in r["message"]


def test_socket_link_decisions_and_abort():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    seen = []

    def app_side():
        c, _ = srv.accept()
        f = c.makefile("rwb", buffering=0)
        hello = json.loads(f.readline())
        seen.append(hello)
        for _ in range(2):
            m = json.loads(f.readline())
            seen.append(m)
            ok = m["tool"] == "fs_list"
            f.write((json.dumps({"op": "decision", "id": m["id"], "allow": ok, "reason": "" if ok else "nope"}) + "\n").encode())
        f.write(b'{"op": "abort"}\n')
        f.readline()
        c.close()

    t = threading.Thread(target=app_side, daemon=True)
    t.start()
    lk = link.SocketLink(port, "tok")
    assert lk.begin("fs_list", {"path": "~"}, {})[:2] == (True, "")
    assert lk.begin("fs_delete", {"path": "~"}, {})[:2] == (False, "nope")
    lk.end(2, True)
    t.join(5)
    assert seen[0]["token"] == "tok" and seen[1]["tool"] == "fs_list"
    assert lk.aborted.is_set()
    with pytest.raises(link.Aborted):
        lk.check()
