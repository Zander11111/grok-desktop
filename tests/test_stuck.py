"""A wedged/slow grok must never leave the app frozen: diagnostics, watchdog banner, Stop escalation, agent restart that resumes
the session, huge notification bursts, and a very long chat staying fast."""
import os
import time

import pytest
from PySide6.QtTest import QTest

from helpers import app, fake_env, pump, received, wait_until


@pytest.fixture
def dapp(tmp_path, monkeypatch):
    monkeypatch.setenv("GD_WATCHDOG_SECS", "1.5")
    monkeypatch.setenv("GD_STOP_KILL_SECS", "1.5")
    qa = app()
    home = fake_env(tmp_path)
    from grok_desktop import paths, theme
    from grok_desktop.app import DesktopApp
    theme.apply(qa)
    s = paths.Settings()
    s["tray"] = False
    s["permission_mode"] = "all"
    s["allow_all_warned"] = True
    d = DesktopApp(qa, show=True)
    d.home = home
    assert wait_until(lambda: d.ctl.state == "ready")
    yield d
    d.shutdown()
    d.win.close()


def log_text(ctl):
    return "\n".join(ctl.log)


def test_prompt_start_end_are_logged_with_stop_reason(dapp):
    c = dapp.ctl
    c.send("hello there")
    assert wait_until(lambda: not c.busy and c.state == "ready", 15)
    t = log_text(c)
    assert "prompt #" in t and " start session=" in t and "stopReason=end_turn" in t and "took=" in t


def test_watchdog_banner_then_restart_agent_resumes_session_and_next_prompt_works(dapp):
    c, w = dapp.ctl, dapp.win
    c.send("first message")
    assert wait_until(lambda: not c.busy and c.state == "ready", 15)
    sid = c.acp.session_id
    c.send("hangforever please")
    assert wait_until(lambda: c.stuck, 15)                       # no ACP traffic for 1.5 s -> watchdog
    assert wait_until(lambda: w.banner.isVisible() and "seems stuck" in w.banner_text.text(), 5)
    assert {b.text() for b in w.banner_btns} >= {"Stop", "Restart agent", "Copy diagnostics"}
    d = c.diagnostics()
    assert "stuck=True" in d and "pending ACP requests" in d and "session/prompt" in d and "recent ACP messages" in d
    assert "WATCHDOG" in log_text(c)
    next(b for b in w.banner_btns if b.text() == "Restart agent").click()
    assert wait_until(lambda: c.state == "ready" and not c.busy and c.acp.session_id, 20)
    assert not c.stuck and not w.banner.isVisible()
    assert c.acp.session_id == sid                                 # same grok session resumed
    assert any(m.get("method") == "session/resume" for m in received(dapp.home))
    c.send("what did i say")                                       # a prompt after the restart works
    assert wait_until(lambda: not c.busy and c.state == "ready", 15)
    assert "Earlier you said" in c.messages[-1]["text"] or "context" in c.messages[-1]["text"]


def test_stop_escalates_to_restart_when_grok_ignores_cancel(dapp):
    c = dapp.ctl
    c.send("hangforever")
    assert wait_until(lambda: c.busy and any(m["role"] == "assistant" for m in c.messages), 10)
    c.stop()                                                        # fake grok ignores session/cancel
    assert wait_until(lambda: "ignored the cancel" in log_text(c), 10)
    assert wait_until(lambda: c.state == "ready" and not c.busy, 20)
    c.send("hello again")
    assert wait_until(lambda: not c.busy and c.state == "ready" and "Here's an answer" in c.messages[-1]["text"], 15)


def test_huge_notification_burst_is_handled_and_next_prompt_works(dapp):
    c = dapp.ctl
    t = time.time()
    c.send("flood now")
    assert wait_until(lambda: not c.busy and c.state == "ready", 120)
    assert time.time() - t < 60
    assert "Flood done" in c.messages[-1]["text"]
    c.send("and another one")
    assert wait_until(lambda: not c.busy and c.state == "ready" and "Here's an answer" in c.messages[-1]["text"], 20)


def test_very_long_chat_renders_only_recent_messages_quickly(dapp):
    w = dapp.win
    msgs = []
    for i in range(400):
        msgs.append({"id": i * 3, "role": "user", "text": f"step {i}", "attachments": [], "meta": {}})
        msgs.append({"id": i * 3 + 1, "role": "tool", "text": "Run: make", "attachments": [], "meta": {"status": "completed"}})
        msgs.append({"id": i * 3 + 2, "role": "assistant", "attachments": [], "meta": {},
                     "text": "Done. " * 30 + "\n\n```python\n" + "\n".join(f"a{j}=1" for j in range(30)) + "\n```"})
    w.chat.messages = msgs
    t = time.perf_counter()
    w.chat._render()
    assert time.perf_counter() - t < 1.0
    html = w.chat.toPlainText()
    assert "earlier messages" in html and "step 399" in html and "step 5\n" not in html
    from PySide6.QtCore import QUrl
    w.chat._on_link(QUrl("more:"))
    assert "step 399" in w.chat.toPlainText() and w.chat.limit == 270


def test_settings_has_copy_diagnostics(dapp):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QPushButton
    from grok_desktop.ui import SettingsDialog
    d = SettingsDialog(dapp.ctl, dapp.win)
    b = next(x for x in d.findChildren(QPushButton) if x.text() == "Copy diagnostics")
    b.click()
    assert "Grok Desktop" in QGuiApplication.clipboard().text() and "recent ACP messages" in QGuiApplication.clipboard().text()
    assert any(x.text() == "Restart agent" for x in d.findChildren(QPushButton))


def test_live_thinking_indicator_not_stuck_banner(dapp):
    """During a long turn with steady traffic (thought chunks/tool steps) the app shows 'Grok is … 0:NN', never the stuck banner."""
    c, w = dapp.ctl, dapp.win
    c.send("slow please")                                           # ~15 s of streamed words
    assert wait_until(lambda: c.busy, 5)
    assert wait_until(lambda: w.activity.isVisible() and "Grok is" in w.activity.text(), 5)
    assert wait_until(lambda: "0:0" in w.activity.text() or "0:1" in w.activity.text(), 5)
    pump(2.5)                                                       # longer than the 1.5 s test watchdog, but chunks keep arriving
    assert not c.stuck and "seems stuck" not in w.banner_text.text()
    c.stop()
    assert wait_until(lambda: not c.busy, 10)
    assert wait_until(lambda: not w.activity.isVisible(), 3)


def test_activity_shows_thinking_phase_and_stuck_replaces_it(dapp):
    c, w = dapp.ctl, dapp.win
    c.phase, c.phase_label = "thinking", ""
    c.acp._turn_id, c.acp._turn_t0 = 99, time.time() - 42
    w._tick_activity()
    assert "Grok is thinking" in w.activity.text() and "0:42" in w.activity.text()
    c.phase, c.phase_label = "working", "Run: make build"
    w._tick_activity()
    assert "working: Run: make build" in w.activity.text()
    c.stuck = True
    w._tick_activity()
    assert not w.activity.isVisible()                               # the stuck banner takes over (no traffic at all)
    c.stuck = False
    c.acp._turn_id = None
