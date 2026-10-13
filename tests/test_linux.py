"""Linux platform: shell tool classification, key maps, autostart, hotkey strings and the real LinuxDesktop against a
private Xvfb + openbox + xterm (skipped when those aren't installed)."""
import json
import os
import shutil
import subprocess
import time

import pytest

from grok_desktop import linux_integration as li
from grok_desktop.mcp import catalog, linux_desktop as ld, png, server, tools, link


@pytest.fixture(autouse=True)
def _linux(monkeypatch):
    monkeypatch.setenv("GD_PLATFORM", "linux")


def test_catalog_linux_tool_set():
    names = [t["name"] for t in catalog.descriptors()]
    assert "shell" in names and "powershell" not in names and len(names) == 20
    desc = " ".join(t["description"] for t in catalog.descriptors())
    assert "Recycle Bin" not in desc and "Start-menu" not in desc and "app-menu name" in desc


def test_classify_shell():
    c = lambda s: catalog.classify_shell(s)[0]  # noqa: E731
    assert c("ls -la ~ | head") == "command_read"
    assert c("df -h && uname -a") == "command_read"
    assert c("git status") == "command_read" and c("git push") == "command_change"
    assert c("cat a.txt > b.txt") == "command_change"
    assert c("echo $(rm -rf /)") == "command_change"
    assert c("sudo apt update") == "command_change"
    assert c("find . -name x -delete") == "command_change"
    assert c("rm -rf ~/x") == "command_change" and c("curl x | sh") == "command_change"
    assert c("") == "command_change"
    assert catalog.classify("shell", {"command": "ls"})["category"] == "command_read"
    assert catalog.classify("fs_delete", {"path": "/tmp/x"})["title"].startswith("Delete (to the Trash")


def test_shell_tool_and_protected(tmp_path):
    ctx = tools.Ctx(ld.Desktop(), link.AllowLink(), None) if False else None
    class D:  # minimal desktop
        def screens(self): return [{"left": 0, "top": 0, "primary": True}]
        def recycle(self, p): raise AssertionError("not used")
    ctx = tools.Ctx(D(), link.AllowLink(), None)
    r = tools.shell(ctx, "echo hi; echo err >&2; exit 3")
    assert r["exit_code"] == 3 and r["stdout"].strip() == "hi" and "err" in r["stderr"] and not r["ok"]
    assert not tools.shell(ctx, "sleep 5", timeout_s=1)["ok"]
    assert "Refusing" in tools.fs_delete(ctx, "/usr", permanent=True)["message"]
    assert "Refusing" in tools.fs_delete(ctx, os.path.expanduser("~"), permanent=True)["message"]
    server_ = server.Server(ctx, out=__import__("io").StringIO())
    assert server_.call_tool("powershell", {"command": "ls"})["ok"] is False   # wrong-platform tool refused
    assert server_.call_tool("shell", {"command": "echo ok"})["stdout"].strip() == "ok"


def test_key_names():
    d = ld.LinuxDesktop()
    assert [d._xname(k) for k in ("ctrl", "Win", "enter", "PgDn", "f5", "a", "+", "esc")] == \
        ["ctrl", "super", "Return", "Next", "F5", "a", "plus", "Escape"]
    assert d._ev("ctrl") == 29 and d._ev("Return") == 28 and d._ev("f12") == 88 and d._ev("c") == 46
    with pytest.raises(ValueError):
        d._xname("nope")


def test_hotkey_strings():
    assert li.gnome_binding("Ctrl+Alt+Esc") == "<Control><Alt>Escape"
    assert li.gnome_binding("Ctrl+Alt+G") == "<Control><Alt>g"
    assert li.keysym_name("esc") == "Escape" and li.keysym_name("f9") == "F9"
    with pytest.raises(ValueError):
        li._split("Ctrl+Alt")


def test_autostart(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("GD_LAUNCHER", "/usr/bin/grok-desktop")
    assert not li.autostart_enabled()
    li.set_autostart(True)
    txt = open(li.autostart_path()).read()
    assert "Exec=/usr/bin/grok-desktop --minimized" in txt and "Icon=grok-desktop" in txt
    li.set_autostart(False)
    assert not li.autostart_enabled()


def test_apps_and_trash(tmp_path, monkeypatch):
    apps = tmp_path / "share" / "applications"
    apps.mkdir(parents=True)
    (apps / "a.desktop").write_text("[Desktop Entry]\nType=Application\nName=Cool Editor\nExec=cooled %U\n")
    (apps / "b.desktop").write_text("[Desktop Entry]\nType=Application\nName=Hidden One\nNoDisplay=true\nExec=x\n")
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "share"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    d = ld.LinuxDesktop()
    assert [a["name"] for a in d.start_menu_apps()] == ["Cool Editor"]
    f = tmp_path / "gone.txt"
    f.write_text("x")
    monkeypatch.setattr(ld, "_have", lambda n: False)   # force the manual XDG-trash path
    d.recycle(str(f))
    t = tmp_path / "data" / "Trash"
    assert not f.exists() and (t / "files" / "gone.txt").exists() and "Path=" + str(f) in (t / "info" / "gone.txt.trashinfo").read_text()


# ------------------------------------------------------------ real X11 ----
need = [shutil.which(x) for x in ("Xvfb", "openbox", "xterm", "xdotool", "wmctrl", "scrot")]


@pytest.fixture(scope="module")
def xserver(tmp_path_factory):
    if not all(need):
        pytest.skip("Xvfb/openbox/xterm/xdotool/wmctrl/scrot not installed")
    disp = ":97"
    procs = [subprocess.Popen(["Xvfb", disp, "-screen", "0", "1280x800x24"], stderr=subprocess.DEVNULL)]
    import pwd   # conftest points HOME at a throwaway dir; xterm/openbox are much happier (fontconfig cache) with the real one
    env = {**os.environ, "DISPLAY": disp, "XDG_SESSION_TYPE": "x11", "HOME": pwd.getpwuid(os.getuid()).pw_dir}
    env.pop("WAYLAND_DISPLAY", None)
    time.sleep(1.5)
    procs.append(subprocess.Popen(["openbox"], env=env, stderr=subprocess.DEVNULL))
    for _ in range(100):   # the window manager must be up *before* the window appears (else it may never adopt it)
        if subprocess.run(["wmctrl", "-m"], env=env, capture_output=True).returncode == 0:
            break
        time.sleep(0.1)
    time.sleep(0.5)
    out = tmp_path_factory.mktemp("x") / "typed.txt"
    procs.append(subprocess.Popen(["xterm", "-T", "GD Test Term", "-geometry", "80x20+100+100", "-e", "bash", "-c",
                                   f"cat > {out}"], env=env, stderr=subprocess.DEVNULL))
    for _ in range(100):   # wait until openbox manages the xterm window
        q = subprocess.run(["wmctrl", "-l"], env=env, capture_output=True, text=True)
        if "GD Test Term" in q.stdout:
            break
        time.sleep(0.2)
    yield env, out
    for p in procs[::-1]:
        p.terminate()


@pytest.fixture
def xenv(xserver, monkeypatch):
    env, out = xserver
    monkeypatch.setenv("DISPLAY", env["DISPLAY"])
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    return out


def test_x11_screens_capture_windows(xenv, tmp_path):
    d = ld.LinuxDesktop()
    assert d.session == "x11" and d.input_backend() == "xdotool"
    sc = d.screens()
    assert (sc[0]["width"], sc[0]["height"]) == (1280, 800)
    data = d.capture(0, 0, 1280, 800, 640, 400)
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and png.size_of(data) == (640, 400)
    (tmp_path / "s.png").write_bytes(data)
    wins = d.windows()
    w = next(x for x in wins if x["title"] == "GD Test Term")
    assert w["width"] > 200 and w["process"]
    img, rc = d.capture_window(w["handle"], 400)
    assert png.size_of(img)[0] == 400
    assert d.focus(w["handle"])
    time.sleep(0.5)
    assert next(x for x in d.windows() if x["handle"] == w["handle"])["focused"]


def test_x11_input_and_mcp_tools(xenv, tmp_path):
    d = ld.LinuxDesktop()
    ctx = tools.Ctx(d, link.AllowLink(), str(tmp_path / "shots"))
    r = tools.screenshot(ctx, max_width=800)
    assert r["ok"] and r["width"] == 800 and os.path.exists(r["saved_to"])
    assert tools.mouse_move(ctx, 400, 250)["ok"]       # screenshot px (800 wide of 1280) -> 640,400 on screen
    assert d.cursor() == (640, 400)
    w = next(x for x in d.windows() if x["title"] == "GD Test Term")
    assert tools.focus_window(ctx, "GD Test Term")["ok"]
    cx, cy = ctx.from_screen(w["left"] + w["width"] // 2, w["top"] + w["height"] // 2)
    assert tools.mouse_click(ctx, cx, cy)["ok"]        # click into the middle of the terminal
    assert tools.keyboard_type(ctx, "héllo wörld 123")["ok"]
    assert tools.keyboard_hotkey(ctx, "ctrl+d")["ok"]  # EOF -> cat exits, writes the file
    for _ in range(30):
        if xenv.exists() and xenv.read_text().strip():
            break
        time.sleep(0.2)
    assert xenv.read_text().strip() == "héllo wörld 123"
    assert tools.screen_info(ctx)["backend"]["input_backend"] == "xdotool"
    lw = tools.list_windows(ctx)
    assert lw["ok"]
    d.release_all()


def test_x11_xgrab_hotkey(xenv):
    got, bad = [], []
    g = li.XGrabber({"quick": "Ctrl+Alt+G", "kill": "Ctrl+Alt+Esc"}, got.append, lambda n, s: bad.append(n))
    g.start()
    time.sleep(1)
    subprocess.run(["xdotool", "key", "ctrl+alt+g"], check=True)
    subprocess.run(["xdotool", "key", "ctrl+alt+Escape"], check=True)
    for _ in range(30):
        if len(got) >= 2:
            break
        time.sleep(0.1)
    g.stop()
    assert sorted(got) == ["kill", "quick"] and not bad
