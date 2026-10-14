"""Widget tests (offscreen Qt) for the main window, settings, permission card, indicator and quick ask."""
import os

import pytest
from PySide6.QtCore import QMimeData, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtTest import QTest

from helpers import app, fake_env, pump, wait_until


@pytest.fixture
def dapp(tmp_path):
    qa = app()
    home = fake_env(tmp_path)
    from grok_desktop import paths, theme
    from grok_desktop.app import DesktopApp
    theme.apply(qa)
    s = paths.Settings()
    s["tray"] = False
    d = DesktopApp(qa, show=True)
    d.home = home
    assert wait_until(lambda: d.ctl.state == "ready")
    yield d
    d.shutdown()
    d.win.close()


def type_and_send(d, text):
    d.win.input.setFocus()
    d.win.input.setPlainText(text)
    QTest.keyClick(d.win.input, Qt.Key_Return)
    assert wait_until(lambda: d.ctl.busy, 5) or True
    assert wait_until(lambda: not d.ctl.busy and d.ctl.state == "ready", 15)
    pump(0.15)


def test_send_render_and_sidebar(dapp):
    w = dapp.win
    type_and_send(dapp, "show me python please")
    assert w.input.toPlainText() == ""
    assert w.list.count() == 1 and w.list.item(0).text() == "show me python please"
    assert w.title.text() == "show me python please"
    html = w.chat.toHtml()
    assert "hello from grok" in html and "Copy" in html
    # copy button puts the code on the clipboard
    w.chat._on_link(QUrl("copy:0"))
    assert QGuiApplication.clipboard().text() == 'print("hello from grok")'
    w.chat._on_link(QUrl(f"copymsg:{dapp.ctl.messages[-1]['id']}"))
    assert "Here's an answer" in QGuiApplication.clipboard().text()
    # new chat / search / rename / delete
    w.new_chat()
    assert w.title.text() == "New chat" and "Ask anything" in w.chat.toPlainText() + w.chat.toHtml()
    type_and_send(dapp, "second one about dogs")
    assert w.list.count() == 2
    w.search.setText("dogs")
    assert w.list.count() == 1
    w.search.setText("")
    cid = w.list.item(1).data(Qt.UserRole)
    dapp.ctl.rename_chat(cid, "Renamed!")
    assert any(w.list.item(i).text() == "Renamed!" for i in range(w.list.count()))
    w.delete_chat(cid, confirm=False)
    assert w.list.count() == 1
    w.list.itemClicked.emit(w.list.item(0))
    assert dapp.ctl.current == w.list.item(0).data(Qt.UserRole)


def test_attachments_drop_paste_and_inline_image(dapp, tmp_path):
    w = dapp.win
    t = tmp_path / "notes.md"
    t.write_text("# hi\n")
    w.add_attachments([str(t)])
    img = QImage(40, 30, QImage.Format_RGB32)
    img.fill(QColor("#7C5CFF"))
    md = QMimeData()
    md.setImageData(img)
    w.input.insertFromMimeData(md)          # paste from the clipboard
    assert len(w.attachments) == 2 and w.chips_w.isVisibleTo(w)
    assert os.path.basename(w.attachments[1]).startswith("pasted-")
    w.remove_attachment(str(t))
    assert len(w.attachments) == 1
    w.add_attachments([str(t)])
    dapp.ctl.auto_answer = lambda a: "once"
    type_and_send(dapp, "what are these?")
    assert w.attachments == [] and not w.chips_w.isVisibleTo(w)
    html = w.chat.toHtml()
    assert "<img" in html and "notes.md" in html
    assert "image via view_image" in w.chat.toPlainText()


def test_permission_card_and_stop_button(dapp, tmp_path):
    w = dapp.win
    victim = tmp_path / "x.txt"
    victim.write_text("x")
    w.input.setPlainText(f"delete {victim}")
    w.send()
    assert wait_until(lambda: w.perm.isVisibleTo(w), 10)
    assert "Delete (to the Recycle Bin)" in w.perm.head.text()
    assert not w.perm.chat.isVisibleTo(w)    # no "allow for this chat" for deletes
    assert w.send_btn.text() == "■"          # Send became Stop
    w.perm.deny.click()
    assert wait_until(lambda: not dapp.ctl.busy, 10)
    pump(0.1)
    assert victim.exists() and not w.perm.isVisibleTo(w) and w.send_btn.text() == "↑"
    w.input.setPlainText("answer slow please")
    w.send()
    assert wait_until(lambda: "word3" in w.chat.toPlainText(), 10)
    w.send_btn.click()                       # Stop
    assert wait_until(lambda: not dapp.ctl.busy, 10)
    pump(0.1)
    assert "Stopped." in w.chat.toPlainText()


def test_settings_allow_everything_warning(dapp):
    from grok_desktop.ui import SettingsDialog
    d = SettingsDialog(dapp.ctl, dapp.win)
    assert d.mode_buttons["ask"].isChecked()
    QTimer.singleShot(200, lambda: d.warning_box.reject())
    d.mode_buttons["all"].setChecked(True)   # warning -> Cancel
    pump(0.3)
    assert dapp.ctl.mode == "ask" and d.mode_buttons["ask"].isChecked()
    assert not dapp.ctl.settings["allow_all_warned"]

    def accept():
        box = d.warning_box
        next(b for b in box.buttons() if b.text().startswith("Yes")).click()
    QTimer.singleShot(200, accept)
    d.mode_buttons["all"].setChecked(True)
    pump(0.3)
    assert dapp.ctl.mode == "all" and dapp.ctl.settings["allow_all_warned"]
    d.mode_buttons["safe"].setChecked(True)
    assert dapp.ctl.mode == "safe"
    assert [d.model.itemData(i) for i in range(d.model.count())] == ["grok-4.6", "grok-4.5"]
    d.quick.setText("Ctrl+Alt+Q")
    d.accept()
    assert dapp.ctl.settings["quick_ask_hotkey"] == "Ctrl+Alt+Q"


def test_indicator_and_emergency_stop(dapp):
    dapp.ctl.controlActive.emit(True, "Click at 10, 20")
    assert dapp.indicator.visible and "Click at 10, 20" in dapp.indicator.banner.sub.text()
    dapp.indicator.banner.stop.click()       # the Stop button on the indicator = emergency stop
    pump(0.1)
    assert not dapp.indicator.visible and dapp.ctl.control.killed
    assert "Emergency stop is on" in dapp.win.banner_text.text() and dapp.win.banner.isVisibleTo(dapp.win)
    dapp.win.banner_btns[0].click()          # Resume
    assert not dapp.ctl.control.killed and not dapp.win.banner.isVisibleTo(dapp.win)
    dapp._hotkey("kill")                     # the global hotkey does the same
    assert dapp.ctl.control.killed


def test_quick_ask(dapp):
    dapp.quick.popup()
    assert dapp.quick.isVisible()
    dapp.quick.edit.setText("quick question about space")
    QTest.keyClick(dapp.quick.edit, Qt.Key_Return)
    assert not dapp.quick.isVisible()
    assert wait_until(lambda: dapp.ctl.current and not dapp.ctl.busy and len(dapp.ctl.messages) >= 2, 10)
    assert dapp.ctl.store.get(dapp.ctl.current)["title"] == "quick question about space"


def test_model_picker_in_top_bar(dapp):
    w = dapp.win
    assert [w.model.itemData(i) for i in range(w.model.count())] == ["grok-4.6", "grok-4.5"]
    type_and_send(dapp, "hi")
    w.model.setCurrentIndex(1)
    w.model.activated.emit(1)
    assert wait_until(lambda: dapp.ctl.acp.current_model == "grok-4.5", 5)


def test_banners_for_login(tmp_path):
    qa = app()
    fake_env(tmp_path, logged_in=False)
    from grok_desktop.app import DesktopApp
    d = DesktopApp(qa, show=True)
    assert wait_until(lambda: d.ctl.state == "needs_login")
    pump(0.05)
    assert "Sign in to Grok" in d.win.banner_text.text()
    d.win.banner_btns[1].click()             # "Use a code instead"
    assert wait_until(lambda: "FAKE-" in d.win.banner_text.text(), 10)
    assert wait_until(lambda: d.ctl.state == "ready", 10)
    pump(0.05)
    assert not d.win.banner.isVisibleTo(d.win)
    d.shutdown()


def test_long_chat_scroll_is_stable(dapp):
    """A long chat: scrolling stays put while a streamed re-render happens, identical html is not re-laid-out, wheel scrolling is fast."""
    import time
    w = dapp.win
    msgs = [{"id": i, "role": "user" if i % 2 == 0 else "assistant", "attachments": [], "meta": {},
             "text": f"message {i} " + "lorem ipsum dolor sit amet " * 25 + ("\n\n```python\nprint(1)\n```" if i % 3 == 0 else "")}
            for i in range(300)]
    w.resize(1100, 720)
    w.chat.messages = msgs
    w.chat.limit = 10**6
    t = time.time()
    w.chat._render()
    assert time.time() - t < 10
    sb = w.chat.verticalScrollBar()
    assert sb.maximum() > 20000 and sb.value() >= sb.maximum() - 40   # opened at the bottom
    sb.setValue(sb.maximum() // 2)
    mid = sb.value()
    w.chat._last_scroll = 0
    w.chat._render()                 # identical html -> skipped
    assert sb.value() == mid
    msgs[-1]["text"] += " streamed more"
    w.chat._last_scroll = 0
    w.chat._render()                 # changed html: re-render keeps the scroll position
    assert abs(sb.value() - mid) < 5
    t = time.time()
    for _ in range(100):
        QTest.mouseMove(w.chat.viewport())
        sb.setValue(sb.value() + 120)
        w.chat.viewport().repaint()
    assert time.time() - t < 15


def test_scrolled_chat_paints_like_a_fresh_render(dapp):
    """Regression for the smeared/overlapping text seen on Zorin (0.1.1 set WA_OpaquePaintEvent on the viewport): a viewport grab after
    scrolling around must equal a freshly built view at the same offset."""
    from PySide6.QtCore import Qt
    from grok_desktop.ui import ChatView
    msgs = [{"id": i, "role": "user" if i % 2 == 0 else "assistant", "attachments": [], "meta": {},
             "text": f"message {i}: it should also expand smoothly when the mouse hovers over it. " * (1 + i % 4)
             + ("\n\n```python\nprint(1)\n```" if i % 3 == 0 else "")} for i in range(40)]

    def build():
        v = ChatView()
        v.resize(700, 500)
        v.show()
        v.messages = msgs
        v.limit = 10**6
        v._render()
        pump(0.3)
        return v
    a, b = build(), build()
    assert not a.viewport().testAttribute(Qt.WA_OpaquePaintEvent)
    sb = a.verticalScrollBar()
    for v in (sb.maximum() // 2, 100, 37, 900, 15, 16, 17, 1500, 400):
        sb.setValue(v)
        pump(0.05)
    b.verticalScrollBar().setValue(sb.value())
    pump(0.2)
    ia, ib = a.viewport().grab().toImage(), b.viewport().grab().toImage()
    assert ia.size() == ib.size() and ia == ib
    a.close(), b.close()


def test_welcome_prompts_composer_and_quiet_chrome(dapp):
    """Starter prompts fill the composer, the field grows, and chrome uses text instead of emoji."""
    from PySide6.QtWidgets import QGroupBox, QPushButton
    from grok_desktop.ui import SettingsDialog
    w = dapp.win
    html = w.chat.toHtml()
    assert "Explain this error" in html and "Review a file" in html and "Draft a reply" in html
    assert "📎" not in html
    w.chat._on_link(QUrl("prompt:error"))
    assert w.input.toPlainText().startswith("Explain this error")
    w.resize(1100, 720)
    pump(0.05)
    w.input.setPlainText("one line")
    w.input._fit()
    short = w.input.height()
    w.input.setPlainText("line\n" * 8)
    w.input._fit()
    assert w.input.height() > short
    w.input.clear()
    w.input._fit()
    assert w.input.height() == short
    assert w.status.text() == "Ready" and "🟢" not in w.status.text()
    assert "Ask every time" in w.mode_lbl.text() and "🔒" not in w.mode_lbl.text()
    assert w.attach_btn.text() == "" and not w.attach_btn.icon().isNull()
    assert w.settings_btn.text() == "" and not w.settings_btn.icon().isNull()
    assert w.mic_btn.text() == "Hold to talk" and w.mic_btn.isVisible()
    assert not w.hf_btn.isVisible() and w.voice_more.isVisible()
    w.voice_more.click()
    assert w.hf_btn.isVisible() and w.mute_btn.isVisible() and not w.voice_more.isVisible()
    d = SettingsDialog(dapp.ctl, w)
    d.show()
    pump(0.05)
    titles = {g.title() for g in d.findChildren(QGroupBox)}
    assert {"Permissions", "Model", "Desktop", "Voice", "Connection"} <= titles
    for b in d.findChildren(QPushButton):
        if b.isVisible():
            assert b.mapTo(d, b.rect().topRight()).x() <= d.width() + 8, b.text()
    d.close()
