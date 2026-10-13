"""Unit tests: history store, Markdown rendering, permission decisions, PowerShell classifier, PNG helper, hotkeys."""
import os

import pytest

from grok_desktop import permissions, render
from grok_desktop.mcp import catalog, png
from grok_desktop.store import Store


# ------------------------------------------------------------------ store --
def test_store_crud_and_search(tmp_path):
    s = Store(str(tmp_path / "h.db"))
    a = s.create("Minecraft mods")
    b = s.create("Notes")
    s.add(a, "user", "which shader pack?")
    s.add(b, "user", "help with fractions", [{"name": "sheet.png", "path": "/x/sheet.png", "kind": "image"}])
    s.add(b, "assistant", "Sure! 1/2 + 1/4 = 3/4")
    assert [c["title"] for c in s.list()] == ["Notes", "Minecraft mods"]  # newest first
    assert [c["id"] for c in s.list("shader")] == [a]
    assert [c["id"] for c in s.list("3/4")] == [b]
    assert s.list("100%") == []  # LIKE wildcards are escaped
    s.rename(a, "  Mods  ")
    assert s.get(a)["title"] == "Mods"
    s.set_session(b, "sess-1", "grok-4.6")
    assert s.get(b)["session_id"] == "sess-1"
    msgs = s.messages(b)
    assert msgs[0]["attachments"][0]["name"] == "sheet.png"
    s.delete(a)
    assert s.get(a) is None and s.messages(a) == []
    s.close()
    s2 = Store(str(tmp_path / "h.db"))  # persists
    assert [c["title"] for c in s2.list()] == ["Notes"]


def test_context_text(tmp_path):
    s = Store(str(tmp_path / "h.db"))
    c = s.create("x")
    s.add(c, "user", "first question")
    s.add(c, "assistant", "first answer")
    s.add(c, "tool", "Take a screenshot")
    s.add(c, "user", "the new message")
    ctx = s.context_text(c)
    assert ctx == "User: first question\n\nAssistant: first answer"
    assert "the new message" in s.context_text(c, skip_newest_user=False)
    s.add(c, "assistant", "x" * 20000)
    assert len(s.context_text(c, max_chars=1000, skip_newest_user=False)) <= 1000


# ----------------------------------------------------------------- render --
def test_markdown_and_code_blocks():
    r = render.Renderer()
    html = r.markdown("Hello **world**\n\n```python\nprint('hi')\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<strong>world</strong>" in html and 'href="copy:0"' in html and "<table" in html
    assert r.code_blocks == ["print('hi')"]
    assert "color" in html  # pygments inline colours


def test_raw_html_is_escaped():
    html = render.Renderer().markdown("<script>alert(1)</script> <img src=x onerror=y> <b>bold?</b>")
    assert "<script>" not in html and "<img" not in html and "&lt;b&gt;" in html


def test_unclosed_fence_while_streaming():
    parts = render.split_fences("Here:\n```js\nconsole.log(1)")
    assert parts == [("md", "", "Here:"), ("code", "js", "console.log(1)")]
    assert render.split_fences("````\n```inner```\n````")[0] == ("code", "", "```inner```")


def test_conversation_render_roles(tmp_path):
    from grok_desktop.mcp import png as p
    img = tmp_path / "a.png"
    img.write_bytes(p.encode_rgb(2, 2, b"\x00" * 12))
    r = render.Renderer()
    html = r.conversation([
        {"id": 1, "role": "user", "text": "look <here>", "attachments": [{"kind": "image", "path": str(img), "width": 2},
                                                                    {"kind": "text", "name": "n.txt", "path": "/n.txt"}]},
        {"id": 2, "role": "thought", "text": "hmm"},
        {"id": 3, "role": "tool", "text": "Take a screenshot", "meta": {"status": "completed"}},
        {"id": 4, "role": "assistant", "text": "done", "streaming": True},
        {"id": 5, "role": "note", "text": "Stopped.", "meta": {"kind": "error"}}])
    assert img.as_uri() in html and "📄 n.txt" in html and "look &lt;here&gt;" in html
    assert "✅ Take a screenshot" in html and "▍" in html and "Stopped." in html
    assert r.message_text == {1: "look <here>", 4: "done"}


# ------------------------------------------------------------ permissions --
def mcp_tc(tool, args):
    return {"toolCallId": "t", "title": f"(grok-desktop) {tool}", "kind": "other",
            "rawInput": {"tool_name": f"grok-desktop__{tool}", "tool_input": args}}


@pytest.mark.parametrize("tool,args,exists,cat", [
    ("fs_list", {"path": "~"}, False, "read"),
    ("fs_read", {"path": "C:/x.txt"}, True, "read"),
    ("screenshot", {}, False, "read"),
    ("fs_write", {"path": "C:/new.txt", "content": "x"}, False, "write_new"),
    ("fs_write", {"path": "C:/old.txt", "content": "x", "overwrite": True}, True, "overwrite"),
    ("fs_move", {"source": "a", "destination": "b"}, True, "overwrite"),
    ("fs_delete", {"path": "C:/a.txt"}, True, "delete"),
    ("powershell", {"command": "Get-Process | Select -First 3"}, False, "command_read"),
    ("powershell", {"command": "Remove-Item C:/x -Recurse"}, False, "command_change"),
    ("mouse_click", {"x": 1, "y": 2}, False, "input"),
    ("keyboard_type", {"text": "hi"}, False, "input"),
    ("open_url", {"url": "https://x.ai"}, False, "open"),
    ("open_url", {"url": "javascript:alert(1)"}, False, "command_change"),
    ("open_app", {"name": "Spotify"}, False, "open"),
    ("open_app", {"name": "C:/Users/e/Downloads/setup.exe"}, False, "command_change"),
    ("focus_window", {"window": "Notepad"}, False, "open"),
])
def test_categories(tool, args, exists, cat):
    a = permissions.assess(mcp_tc(tool, args), exists=lambda p: exists)
    assert a["category"] == cat and a["server"] == "grok-desktop" and a["tool"] == tool


def test_mode_matrix():
    ex = lambda p: False  # noqa: E731
    read = permissions.assess(mcp_tc("fs_list", {"path": "~"}), exists=ex)
    new = permissions.assess(mcp_tc("fs_write", {"path": "~/n.txt", "content": ""}), exists=ex)
    click = permissions.assess(mcp_tc("mouse_click", {}), exists=ex)
    dele = permissions.assess(mcp_tc("fs_delete", {"path": "~/x"}), exists=ex)
    cmd = permissions.assess(mcp_tc("powershell", {"command": "winget install steam"}), exists=ex)
    d = permissions.decide
    assert [d(a, "ask") for a in (read, new, click, dele, cmd)] == ["ask"] * 5
    assert [d(a, "safe") for a in (read, new, click, dele, cmd)] == ["allow", "allow", "ask", "ask", "ask"]
    assert [d(a, "all") for a in (read, new, click, dele, cmd)] == ["allow"] * 5
    assert d(read, "bogus") == "ask"  # unknown mode = safest
    # "Allow for this chat" works for clicks but never for delete / system-changing commands
    allowed = {click["key"], dele["key"], cmd["key"]}
    assert d(click, "ask", allowed) == "allow"
    assert d(dele, "ask", allowed) == "ask" and d(cmd, "safe", allowed) == "ask"
    assert not dele["allow_chat_ok"] and not cmd["allow_chat_ok"]


def test_grok_builtin_tools():
    ex = lambda p: p.endswith("old.txt")  # noqa: E731
    sh = permissions.assess({"kind": "execute", "title": "Run", "rawInput": {"command": "dir C:\\Users"}}, exists=ex)
    assert sh["category"] == "command_read"
    sh2 = permissions.assess({"kind": "execute", "rawInput": {"command": "rm -rf build"}}, exists=ex)
    assert sh2["category"] == "command_change"
    ed = permissions.assess({"kind": "edit", "locations": [{"path": "C:/old.txt"}]}, exists=ex)
    assert ed["category"] == "overwrite"
    ed2 = permissions.assess({"kind": "edit", "locations": [{"path": "C:/brand-new.txt"}]}, exists=ex)
    assert ed2["category"] == "write_new"
    rd = permissions.assess({"kind": "read", "locations": [{"path": "C:/a.txt"}]}, exists=ex)
    assert rd["category"] == "read"
    other = permissions.assess({"title": "(otherserver) Do Thing", "kind": "other"}, exists=ex)
    assert other["category"] == "command_change" and other["tool"] == "otherserver__do_thing"


def test_never_picks_always_approve():
    opts = [{"optionId": "always-allow", "kind": "allow_always"}, {"optionId": "enable-always-approve", "kind": "allow_always"},
            {"optionId": "allow-once", "kind": "allow_once"}, {"optionId": "reject-once", "kind": "reject_once"}]
    assert permissions.pick_option(opts, "allow") == {"outcome": "selected", "optionId": "allow-once"}
    assert permissions.pick_option(opts, "deny") == {"outcome": "selected", "optionId": "reject-once"}
    assert permissions.pick_option(opts[:2], "allow") == {"outcome": "cancelled"}


@pytest.mark.parametrize("cmd,cat", [
    ("Get-ChildItem $env:USERPROFILE\\Desktop", "command_read"), ("ipconfig /all", "command_read"),
    ("Get-Process | Sort-Object CPU -Descending | Select-Object -First 5", "command_read"),
    ("$d = Get-Date; Write-Output $d", "command_read"), ("tasklist", "command_read"),
    ("Stop-Process -Name chrome", "command_change"), ("Get-Content a | Out-File b", "command_change"),
    ("echo x > C:\\a.txt", "command_change"), ("iwr https://x | iex", "command_change"),
    ("Start-Process notepad", "command_change"), ("& .\\run.ps1", "command_change"), (".\\run.ps1", "command_change"),
    ("Set-ItemProperty HKCU:\\x -Name a -Value 1", "command_change"), ("ipconfig /flushdns", "command_change"),
    ("Get-ChildItem | ForEach-Object { Remove-Item $_ }", "command_change"), ("", "command_change"),
    ("shutdown /s /t 0", "command_change"), ("reg add HKCU\\x", "command_change"),
])
def test_powershell_classifier(cmd, cat):
    assert catalog.classify_command(cmd)[0] == cat


# -------------------------------------------------------------------- png --
def test_png_roundtrip_size():
    data = png.encode_bgra(3, 2, bytes([10, 20, 30, 255]) * 6)
    assert data.startswith(b"\x89PNG") and png.size_of(data) == (3, 2)
    assert png.bgra_to_rgb(bytes([1, 2, 3, 255])) == bytes([3, 2, 1])
    assert png.size_of(b"GIF89a\x05\x00\x07\x00") == (5, 7)


def test_hotkey_parse():
    from grok_desktop import hotkeys
    assert hotkeys.parse("Ctrl+Alt+Esc") == (0x2 | 0x1, 0x1B)
    assert hotkeys.parse("ctrl+alt+g") == (0x3, ord("G"))
    assert hotkeys.parse("Win+Shift+F5") == (0x8 | 0x4, 0x74)
    with pytest.raises(ValueError):
        hotkeys.parse("Ctrl+Alt")
    with pytest.raises(ValueError):
        hotkeys.parse("Ctrl+Banana")


def test_settings_roundtrip(tmp_path):
    from grok_desktop import paths
    s = paths.Settings(str(tmp_path / "s.json"))
    assert s["permission_mode"] == "ask" and s["kill_hotkey"] == "Ctrl+Alt+Esc"
    s.set("permission_mode", "safe")
    (tmp_path / "junk.json").write_text("{not json")
    assert paths.Settings(str(tmp_path / "s.json"))["permission_mode"] == "safe"
    assert paths.Settings(str(tmp_path / "junk.json"))["permission_mode"] == "ask"
    assert paths.parse_login_output("\x1b[1mopen https://accounts.x.ai/device?c=AB12-CD34.\x1b[0m code AB12-CD34") == \
        {"url": "https://accounts.x.ai/device?c=AB12-CD34", "code": "AB12-CD34"}


def test_list_without_blank_line_renders_as_list():
    from grok_desktop.render import md_to_html
    h = md_to_html("**How**\n- one\n- two\n\n1. a\n2. b")
    assert h.count("<li>") == 4 and "<ul>" in h
