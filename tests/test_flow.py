"""End to end: Controller + AcpClient + the fake grok + the real grok-desktop MCP server (fake desktop)."""
import os

import pytest

from helpers import actions, app, fake_env, pump, received, wait_until


@pytest.fixture
def ctl(tmp_path):
    app()
    home = fake_env(tmp_path)
    from grok_desktop import paths
    from grok_desktop.controller import Controller
    c = Controller(paths.Settings())
    c.fake_home = home
    c.tmp = tmp_path
    c.start()
    assert wait_until(lambda: c.state == "ready"), c.log
    yield c
    c.shutdown()


def last(c, role):
    return next((m for m in reversed(c.messages) if m["role"] == role), None)


def said(c):
    """All assistant text since the last user message (tool calls split it into bubbles)."""
    out = []
    for m in reversed(c.messages):
        if m["role"] == "user":
            break
        if m["role"] == "assistant":
            out.insert(0, m["text"])
    return " ".join(out)


def send_and_wait(c, text, atts=None, timeout=20):
    assert c.send(text, atts)
    assert wait_until(lambda: c.acp.busy, 5) or not c.acp.busy
    assert wait_until(lambda: not c.acp.busy and c.state == "ready", timeout), c.log[-20:]
    pump(0.05)


def test_chat_streams_and_persists(ctl):
    send_and_wait(ctl, "hello there")
    a = last(ctl, "assistant")
    assert "Here's an answer to" in a["text"] and "```python" in a["text"]
    convs = ctl.conversations()
    assert len(convs) == 1 and convs[0]["title"] == "hello there"
    stored = ctl.store.messages(convs[0]["id"])
    assert [m["role"] for m in stored if m["role"] in ("user", "assistant")] == ["user", "assistant"]
    assert convs[0]["session_id"]
    # sent as plain ACP text, with our rules + MCP server in session/new
    new = received(ctl.fake_home, "session/new")[0]["params"]
    assert new["mcpServers"][0]["name"] == "grok-desktop" and "Grok Desktop" in new["_meta"]["rules"]
    assert new["_meta"]["yoloMode"] is False


def test_history_resume_across_restart(ctl):
    send_and_wait(ctl, "my favourite game is Valheim")
    cid = ctl.current
    sid = ctl.store.get(cid)["session_id"]
    ctl.acp.stop()
    ctl.current = ""
    ctl.start()
    assert wait_until(lambda: ctl.state == "ready")
    ctl.open_chat(cid)
    assert wait_until(lambda: ctl.acp.session_id == sid), ctl.log[-10:]
    assert received(ctl.fake_home, "session/resume")[-1]["params"]["sessionId"] == sid
    send_and_wait(ctl, "what did i say before?")
    assert "Valheim" in said(ctl)


def test_resume_failure_resends_context(ctl):
    send_and_wait(ctl, "remember the code word PINEAPPLE")
    cid = ctl.current
    (ctl.fake_home / "resume_fail").write_text("1")
    ctl.new_chat()
    assert wait_until(lambda: ctl.state == "ready")
    ctl.open_chat(cid)
    assert wait_until(lambda: ctl.state == "ready" and ctl.acp.session_id)
    assert received(ctl.fake_home, "session/load")  # tried resume, then load, then new
    send_and_wait(ctl, "what did i say?")
    assert "PINEAPPLE" in said(ctl)
    prompt = received(ctl.fake_home, "session/prompt")[-1]["params"]["prompt"][0]["text"]
    assert prompt.startswith("[Earlier in this conversation") and "what did i say?" not in prompt.split("[End")[0]


def test_stop_button_cancels(ctl):
    assert ctl.send("please answer slow")
    assert wait_until(lambda: (last(ctl, "assistant") or {}).get("text", "").count("word") >= 2, 10)
    ctl.stop()
    assert wait_until(lambda: not ctl.acp.busy, 10)
    pump(0.1)
    assert last(ctl, "note")["text"] == "Stopped."
    assert "word59" not in said(ctl)


def test_text_and_image_attachments(ctl, tmp_path):
    t = tmp_path / "notes.txt"
    t.write_text("line one\nline two\n")
    from grok_desktop.mcp import png
    img = tmp_path / "cat.png"
    img.write_bytes(png.encode_rgb(4, 3, b"\x80\x40\x20" * 12))
    ctl.auto_answer = lambda a: "once"
    send_and_wait(ctl, "what's in these?", [str(t), str(img)])
    user = last(ctl, "user")
    kinds = {a["name"]: a["kind"] for a in user["attachments"]}
    assert kinds == {"notes.txt": "text", "cat.png": "image"}
    assert all(os.path.exists(a["path"]) and "attachments" in a["path"] for a in user["attachments"])
    assert user["attachments"][1]["width"] == 4
    reply = said(ctl)
    assert "file" in reply and "notes.txt" in reply and "(18 chars)" in reply
    assert "image via view_image: Image" in reply and "(1 image)" in reply   # image went back through MCP
    blocks = received(ctl.fake_home, "session/prompt")[-1]["params"]["prompt"]
    assert [b["type"] for b in blocks] == ["text", "resource"]  # real grok: image=false -> no image block


def test_image_block_when_agent_supports_it(tmp_path):
    app()
    fake_env(tmp_path)
    os.environ["MOCK_IMAGES"] = "1"
    from grok_desktop import paths
    from grok_desktop.controller import Controller
    from grok_desktop.mcp import png
    c = Controller(paths.Settings())
    c.start()
    assert wait_until(lambda: c.state == "ready")
    img = tmp_path / "x.png"
    img.write_bytes(png.encode_rgb(2, 2, b"\x00" * 12))
    send_and_wait(c, "look", [str(img)])
    assert "inline image (image/png)" in said(c)
    c.shutdown()
    os.environ.pop("MOCK_IMAGES")


def test_permission_prompt_allow_and_deny(ctl, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.txt").write_text("x")
    asked = []
    ctl.auto_answer = lambda a: asked.append(a) or "deny"
    send_and_wait(ctl, f"list {tmp_path / 'docs'}")
    assert asked and asked[0]["title"] == "List a folder" and asked[0]["category"] == "read"
    assert "no result" in said(ctl)
    assert any(m["role"] == "note" and m["text"].startswith("Denied: List a folder") for m in ctl.messages)
    ctl.auto_answer = lambda a: "once"
    send_and_wait(ctl, f"list {tmp_path / 'docs'}")
    assert "a.txt" in said(ctl)


def test_safe_mode_auto_allows_reads_but_asks_for_delete(ctl, tmp_path):
    victim = tmp_path / "old.txt"
    victim.write_text("bye")
    assert ctl.set_mode("safe")
    asked = []
    ctl.auto_answer = lambda a: asked.append(a["title"]) or "deny"
    send_and_wait(ctl, f"list {tmp_path}")
    assert asked == [] and "old.txt" in said(ctl)
    send_and_wait(ctl, f"delete {victim}")
    assert asked == ["Delete (to the Recycle Bin)"] and victim.exists()
    send_and_wait(ctl, "run Get-ChildItem")  # read-only command: no question
    assert asked == ["Delete (to the Recycle Bin)"]
    send_and_wait(ctl, "click the button")    # screenshot auto, mouse asks
    assert asked[-1] == "Click"


def test_allow_everything_needs_warning(ctl, tmp_path):
    assert not ctl.set_mode("all")
    ctl.acknowledge_allow_all()
    assert ctl.set_mode("all")
    victim = tmp_path / "gone.txt"
    victim.write_text("x")
    ctl.auto_answer = lambda a: pytest.fail("should not ask in Allow everything")
    send_and_wait(ctl, f"delete {victim}")
    assert not victim.exists()
    assert (tmp_path / "desktop" / "RecycleBin").exists()  # went to the (fake) Recycle Bin
    assert any("Allowed automatically" in m["text"] for m in ctl.messages if m["role"] == "note")


def test_screenshot_reaches_model_and_click_shows_indicator(ctl):
    events = []
    ctl.controlActive.connect(lambda on, d: events.append((on, d)))
    ctl.auto_answer = lambda a: "once"
    send_and_wait(ctl, "click the button")
    assert "I can see your screen (1280×720, 1 image)" in said(ctl)
    assert "Clicked the button" in said(ctl)
    assert events and events[0][0] is True and "Click" in events[0][1]
    assert wait_until(lambda: events[-1][0] is False, 5)
    acts = [a for a in actions(ctl.tmp) if a["op"] in ("button", "capture")]
    assert acts[0]["op"] == "capture" and acts[-1] == {**acts[-1], "op": "button", "down": False}
    # one prompt per action: the MCP call reused the ACP approval (no second question)
    notes = [m["text"] for m in ctl.messages if m["role"] == "note"]
    assert sum(n.startswith("Allowed: Click") for n in notes) == 1


def test_tool_call_without_acp_permission_is_still_checked(ctl, tmp_path):
    victim = tmp_path / "keep.txt"
    victim.write_text("x")
    asked = []
    ctl.auto_answer = lambda a: asked.append(a["title"]) or "deny"
    send_and_wait(ctl, f"sneaky delete {victim}")
    assert victim.exists() and asked == ["Delete (to the Recycle Bin)"]
    assert "Not allowed" in said(ctl)


def test_emergency_stop_halts_control(ctl):
    ctl.auto_answer = lambda a: "chat"
    killed = []
    ctl.killedChanged.connect(killed.append)
    assert ctl.send("drag things around")
    assert wait_until(lambda: any(a["op"] == "button" for a in actions(ctl.tmp)), 10)
    ctl.emergency_stop()
    assert wait_until(lambda: not ctl.acp.busy, 10)
    n = len(actions(ctl.tmp))
    pump(0.5)
    acts = actions(ctl.tmp)
    assert len(acts) == n, "no more mouse actions after the emergency stop"
    downs = [a for a in acts if a["op"] == "button"]
    assert downs[-1]["down"] is False   # the mouse button was released
    assert killed == [True] and ctl.control.killed
    assert any("Emergency stop" in m["text"] for m in ctl.messages if m["role"] == "note")
    # a new message re-enables control
    ctl.auto_answer = lambda a: "once"
    send_and_wait(ctl, "click the button")
    assert not ctl.control.killed and "Clicked the button" in said(ctl)


def test_models_and_switch(ctl):
    assert [m["modelId"] for m in ctl.models()] == ["grok-4.6", "grok-4.5"]
    send_and_wait(ctl, "hi")
    ctl.set_model("grok-4.5")
    assert wait_until(lambda: ctl.acp.current_model == "grok-4.5")
    send_and_wait(ctl, "which model?")
    assert "(model grok-4.5)" in said(ctl)
    assert ctl.settings["model"] == "grok-4.5"


def test_model_switch_fallback_restarts_with_flag(ctl):
    (ctl.fake_home / "no_set_model").write_text("1")
    send_and_wait(ctl, "hi")
    sid = ctl.acp.session_id
    ctl.set_model("grok-4.5")
    assert wait_until(lambda: len(received(ctl.fake_home, "initialize")) == 2 and ctl.state == "ready"
                      and ctl.acp.session_id == sid, 15)   # restarted with --model, same conversation resumed
    send_and_wait(ctl, "which model?")
    assert "(model grok-4.5)" in said(ctl)


def test_rename_delete_search(ctl):
    send_and_wait(ctl, "first chat about cats")
    a = ctl.current
    ctl.new_chat()
    send_and_wait(ctl, "second chat about dogs")
    assert len(ctl.conversations()) == 2
    assert [c["id"] for c in ctl.conversations("cats")] == [a]
    ctl.rename_chat(a, "Feline facts")
    assert ctl.store.get(a)["title"] == "Feline facts"
    assert [c["title"] for c in ctl.conversations("feline")] == ["Feline facts"]
    ctl.delete_chat(a)
    assert [c["title"] for c in ctl.conversations()] == ["second chat about dogs"]


def test_sign_in_flow(tmp_path):
    app()
    fake_env(tmp_path, logged_in=False)
    from grok_desktop import paths
    from grok_desktop.controller import Controller
    c = Controller(paths.Settings())
    infos = []
    c.loginInfo.connect(lambda u, code: infos.append((u, code)))
    c.start()
    assert wait_until(lambda: c.state == "needs_login")
    c.start_login(device_code=True)
    assert wait_until(lambda: c.state == "ready", 15), c.log[-10:]
    assert infos[0][0].startswith("https://accounts.x.ai/") and infos[0][1].startswith("FAKE-")
    c.shutdown()


def test_no_grok(tmp_path):
    app()
    fake_env(tmp_path)
    os.environ["GD_GROK_BIN"] = ""
    os.environ["PATH"] = "/nonexistent"
    from grok_desktop import paths
    from grok_desktop.controller import Controller
    c = Controller(paths.Settings())
    c.start()
    assert c.state == "no_grok"
    assert not c.send("hi")
    c.shutdown()
