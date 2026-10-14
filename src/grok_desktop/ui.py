"""Grok Desktop's windows: main window (sidebar + chat), permission card, settings, quick-ask box."""
from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QEvent, QMimeData, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QImage, QKeySequence, QPixmap, QTextCursor
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSlider,
                               QSizePolicy, QTextBrowser, QVBoxLayout, QWidget)

from . import APP_NAME, __version__, paths, permissions
from .controller import Controller
from .render import DOC_CSS, Renderer, starter_text, welcome_html
from .theme import B, BORDER as BORDER_C, MUTED, PANEL as PANEL_BG, V, app_icon, file_icon, paperclip_icon, settings_icon
from .voice import models as vmodels
from .voice.manager import SPEAK_LABELS, SPEAK_MODES, VoiceManager
from .voice.orb import Orb

STATE_TEXT = {"stopped": "Not connected", "starting": "Starting grok…",
              "connecting": "Connecting…", "ready": "Ready", "busy": "Grok is answering…",
              "needs_login": "Sign in needed", "error": "grok stopped", "no_grok": "grok not installed",
              "logging_in": "Waiting for sign-in…", "installing": "Installing grok…"}
STATE_COLOR = {"stopped": "#8C88A3", "starting": "#E6B450", "connecting": "#E6B450", "ready": "#3DDC97",
               "busy": "#A996FF", "needs_login": "#FF6B7A", "error": "#FF6B7A", "no_grok": "#FF6B7A",
               "logging_in": "#E6B450", "installing": "#E6B450"}
MODE_LABEL = {"ask": "Ask every time", "safe": "Safe actions", "all": "Allow everything"}
MODE_COLOR = {"ask": MUTED, "safe": "#7DDE9A", "all": "#F0B429"}

ALLOW_ALL_WARNING = (
    "<b>Allow everything</b> lets Grok do <b>anything</b> on this " + paths.PC_NAME + " without asking you first:<br><br>"
    "• delete or overwrite your files<br>• run any " + paths.SHELL_NAME + " command (install or remove software, change settings)"
    "<br>• move your mouse, click and type in any app, including your browser, Discord or Steam<br><br>"
    "AI can make mistakes, and a web page or file it reads could try to trick it. Only use this when you're watching."
    "<br><br>You can always stop it with <b>{hotkey}</b> or the red Stop button at the top of the screen.<br><br>"
    "Turn on Allow everything?")


# ================================================================ chat view ==
class ChatView(QTextBrowser):
    promptChosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chat")
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._on_link)
        self.document().setDefaultStyleSheet(DOC_CSS)
        self.document().setDocumentMargin(24)
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.renderer = Renderer()
        self.messages: list[dict] = []
        self._timer = QTimer(self, singleShot=True, interval=60, timeout=self._render)
        self._last_html = None
        self._last_scroll = 0.0
        self.limit = 120          # only the newest N messages are laid out; "Show earlier" raises it (a 1500-message chat took >1 s per re-render)
        self._render_ms = 0.0
        # NOTE: no WA_OpaquePaintEvent / autoFill tweaks: QTextEdit relies on Qt clearing the viewport before painting text
        # (0.1.1 set WA_OpaquePaintEvent and left stale text layers smeared over each other).
        self.verticalScrollBar().setSingleStep(36)
        self.verticalScrollBar().valueChanged.connect(lambda _v: setattr(self, "_last_scroll", time.time()))

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        """Repaint the whole viewport instead of blitting the old pixels (QWidget.scroll). Blit scrolling is what leaves
        stale/smeared text on some Wayland/XWayland + Mesa setups."""
        self.viewport().update()

    def set_messages(self, messages: list[dict], now: bool = False) -> None:
        self.messages = messages
        if now:
            self._render()
        elif not self._timer.isActive():
            self._timer.start()

    def _render(self) -> None:
        sb = self.verticalScrollBar()
        if time.time() - self._last_scroll < 0.25 and self._last_html is not None:
            self._timer.start()          # never re-layout the document under the user's finger/wheel
            return
        t0 = time.perf_counter()
        hidden = max(0, len(self.messages) - self.limit)
        html = self.renderer.conversation(self.messages[hidden:]) if self.messages else welcome_html()
        if hidden:
            html = (f'<p align="center"><a href="more:">⬆ Show {min(hidden, 150)} earlier messages '
                    f'({hidden} hidden to keep the app fast)</a></p>') + html
        html = f"<div>{html}</div>"
        # re-rendering costs time on the UI thread: during streaming wait at least 3x as long as the last render took
        self._render_ms = (time.perf_counter() - t0) * 1000
        self._timer.setInterval(int(max(60, min(1500, self._render_ms * 3))))
        if html == self._last_html:
            return
        at_bottom = sb.value() >= sb.maximum() - 40
        old = sb.value()
        self._last_html = html
        self.setUpdatesEnabled(False)    # one repaint after layout + scroll restore, not a flash to the top and back
        try:
            self.setHtml(html)
            sb.setValue(sb.maximum() if at_bottom else old)
        finally:
            self.setUpdatesEnabled(True)

    def _on_link(self, url: QUrl) -> None:
        s = url.toString()
        if url.scheme() == "prompt" or s.startswith("prompt:"):
            key = (url.path() or s.split(":", 1)[-1]).lstrip("/")
            text = starter_text(key)
            if text:
                self.promptChosen.emit(text)
            return
        if s.startswith("copy:"):
            n = int(s[5:])
            if 0 <= n < len(self.renderer.code_blocks):
                QGuiApplication.clipboard().setText(self.renderer.code_blocks[n])
                self.window().statusMessage("Code copied")
        elif s.startswith("copymsg:"):
            t = self.renderer.message_text.get(int(s[8:]) if s[8:].isdigit() else 0)
            if t:
                QGuiApplication.clipboard().setText(t)
                self.window().statusMessage("Message copied")
        elif s.startswith("more:"):
            sb = self.verticalScrollBar()
            below = sb.maximum() - sb.value()
            self.limit += 150
            self._last_html = None
            self._render()
            sb.setValue(max(0, sb.maximum() - below))
        elif url.scheme() in ("http", "https", "mailto", "file"):
            QDesktopServices.openUrl(url)


# ================================================================= composer ==
class Composer(QPlainTextEdit):
    submit = Signal()
    pastedImage = Signal(QImage)
    pastedFiles = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Message Grok…  (Enter to send, Shift+Enter for a new line)")
        self.setTabChangesFocus(True)
        self._fitting = False
        self.setMinimumHeight(64)
        self.setFixedHeight(64)
        self.document().contentsChanged.connect(self._fit)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self) -> None:
        """Grow with the draft, up to about five lines, then scroll inside the field."""
        if self._fitting:
            return
        width = self.viewport().width()
        if width < 40:
            return
        self._fitting = True
        try:
            self.document().setTextWidth(width)
            # QPlainTextDocumentLayout reports document height as a line count.
            lines = min(5, max(1, int(round(self.document().size().height()))))
            height = lines * self.fontMetrics().lineSpacing() + 18
            height = max(64, height)
            if self.height() != height:
                self.setFixedHeight(height)
        finally:
            self._fitting = False

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.submit.emit()
            return
        super().keyPressEvent(e)

    def canInsertFromMimeData(self, source: QMimeData) -> bool:
        return source.hasImage() or source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData) -> None:
        if source.hasUrls() and all(u.isLocalFile() for u in source.urls()):
            self.pastedFiles.emit([u.toLocalFile() for u in source.urls()])
        elif source.hasImage():
            img = source.imageData()
            if isinstance(img, QImage) and not img.isNull():
                self.pastedImage.emit(img)
        else:
            super().insertFromMimeData(source)


class Chip(QFrame):
    removed = Signal(str)

    def __init__(self, path: str):
        super().__init__()
        self.setObjectName("chip")
        self.path = path
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 4, 4)
        lay.setSpacing(6)
        ext = os.path.splitext(path)[1].lower()
        pm = QPixmap(path) if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp") else QPixmap()
        icon = QLabel()
        if not pm.isNull():
            icon.setPixmap(pm.scaled(QSize(40, 40), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            icon.setPixmap(file_icon(18).pixmap(18, 18))
        name = QLabel(os.path.basename(path)[:28])
        x = QPushButton("✕")
        x.setObjectName("ghost")
        x.setFixedSize(22, 22)
        x.setToolTip("Remove")
        x.clicked.connect(lambda: self.removed.emit(self.path))
        lay.addWidget(icon)
        lay.addWidget(name)
        lay.addWidget(x)


# ========================================================== permission card ==
class PermissionCard(QFrame):
    answered = Signal(str)  # once | chat | deny

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("permCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        self.head = QLabel()
        self.head.setTextFormat(Qt.RichText)
        self.head.setWordWrap(True)
        self.detail = QPlainTextEdit(readOnly=True)
        self.detail.setObjectName("detail")
        self.detail.setMaximumHeight(90)
        self.why = QLabel()
        self.why.setObjectName("muted")
        self.why.setWordWrap(True)
        row = QHBoxLayout()
        self.queue = QLabel()
        self.queue.setObjectName("muted")
        self.deny = QPushButton("Deny")
        self.chat = QPushButton("Allow for this chat")
        self.once = QPushButton("Allow once")
        self.once.setObjectName("primary")
        self.deny.setShortcut(QKeySequence("Esc"))
        for b, c in ((self.deny, "deny"), (self.chat, "chat"), (self.once, "once")):
            b.clicked.connect(lambda _=False, c=c: self.answered.emit(c))
        row.addWidget(self.queue)
        row.addStretch()
        row.addWidget(self.deny)
        row.addWidget(self.chat)
        row.addWidget(self.once)
        lay.addWidget(self.head)
        lay.addWidget(self.detail)
        lay.addWidget(self.why)
        lay.addLayout(row)
        self.hide()

    def show_request(self, req: dict | None, queued: int = 0) -> None:
        if not req:
            self.hide()
            return
        a = req["assessment"]
        color, label = {"high": ("#FF7A90", "High risk"), "medium": ("#E6B450", "Caution"),
                        "low": ("#A996FF", "Low risk")}.get(a["risk"], ("#E6B450", "Caution"))
        src = "" if a.get("server") != "grok-desktop" else " <span style='color:#8C88A3'>(computer control)</span>"
        self.head.setText(f"<span style='font-size:15px'><span style='color:{color}'>{label}</span>"
                          f"&nbsp;&nbsp;Grok wants to: <b>{a['title']}</b></span>{src}")
        self.detail.setPlainText(a["detail"] or "(no details)")
        self.detail.setVisible(bool(a["detail"]))
        self.why.setText(" · ".join(a["reasons"]) if a["reasons"] else "")
        self.chat.setVisible(bool(a.get("allow_chat_ok")))
        self.queue.setText(f"+{queued} more waiting" if queued else "")
        self.setProperty("risk", a["risk"])
        self.style().unpolish(self)
        self.style().polish(self)
        self.show()
        self.once.setFocus()


# ================================================================= settings ==
class SettingsDialog(QDialog):
    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle(f"{APP_NAME} settings")
        self.resize(620, 640)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        lay = QVBoxLayout(inner)
        t = QLabel("Settings")
        t.setObjectName("h1")
        lay.addWidget(t)

        g = QGroupBox("Permissions")
        gl = QVBoxLayout(g)
        intro = QLabel("What Grok may do on this computer without asking.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        gl.addWidget(intro)
        self.modes = QButtonGroup(self)
        desc = {"ask": "Grok asks before every action. You can allow one kind of action for this chat.",
                "safe": "Reads, screenshots, and new files run freely. Deletes, overwrites, and mouse or keyboard control still ask.",
                "all": "Nothing asks, including deleting files and controlling the mouse. A warning is shown first."}
        self.mode_buttons = {}
        for m in permissions.MODES:
            rb = QRadioButton(permissions.MODE_LABELS[m] + (" (default)" if m == "ask" else ""))
            d = QLabel(desc[m])
            d.setObjectName("muted")
            d.setWordWrap(True)
            d.setContentsMargins(28, 0, 0, 6)
            self.modes.addButton(rb)
            self.mode_buttons[m] = rb
            gl.addWidget(rb)
            gl.addWidget(d)
            rb.toggled.connect(lambda on, m=m: on and self._mode(m))
        self.mode_buttons[ctl.mode].setChecked(True)
        kill = QLabel(f"Emergency stop: <b>{ctl.settings['kill_hotkey']}</b> works anywhere and halts computer "
                      "control at once. A glowing frame and a Stop bar show while Grok uses the mouse or keyboard.")
        kill.setWordWrap(True)
        gl.addWidget(kill)
        self.control = QCheckBox(f"Let Grok use this {paths.PC_NAME} (files, {paths.SHELL_NAME}, screen, mouse/keyboard, windows, apps)")
        self.control.setChecked(bool(ctl.settings.get("computer_control", True)))
        self.control.toggled.connect(self._control)
        gl.addWidget(self.control)
        lay.addWidget(g)

        g2 = QGroupBox("Model")
        g2l = QHBoxLayout(g2)
        self.model = QComboBox()
        self._fill_models()
        self.model.activated.connect(lambda _i: self.ctl.set_model(self.model.currentData()) if self.model.currentData() else None)
        g2l.addWidget(QLabel("Grok model:"))
        g2l.addWidget(self.model, 1)
        lay.addWidget(g2)

        g3 = QGroupBox("Desktop")
        g3l = QVBoxLayout(g3)
        self.tray = QCheckBox("Show an icon in the system tray")
        self.tray.setChecked(bool(ctl.settings.get("tray")))
        self.close_tray = QCheckBox("Closing the window keeps Grok Desktop running in the tray")
        self.close_tray.setChecked(bool(ctl.settings.get("close_to_tray")))
        row = QHBoxLayout()
        self.quick = QLineEdit(ctl.settings.get("quick_ask_hotkey", ""))
        self.quick.setPlaceholderText("e.g. Ctrl+Alt+G (empty = off)")
        row.addWidget(QLabel("Quick-ask hotkey:"))
        row.addWidget(self.quick, 1)
        g3l.addWidget(self.tray)
        g3l.addWidget(self.close_tray)
        self.autostart = None
        if not paths.IS_WINDOWS and sys.platform.startswith("linux"):
            from . import linux_integration as li
            self.autostart = QCheckBox("Start Grok Desktop when I log in (minimized)")
            self.autostart.setChecked(li.autostart_enabled())
            g3l.addWidget(self.autostart)
        g3l.addLayout(row)
        lay.addWidget(g3)

        g4 = QGroupBox("Connection")
        g4l = QVBoxLayout(g4)
        info = QLabel(f"grok: {ctl.grok or 'not found'}  {ctl.grok_version}<br>"
                      f"Signed in with your SuperGrok account through the grok app (no API key is used).<br>"
                      f"History + settings: {paths.data_dir()}")
        info.setObjectName("muted")
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        def copy_diag():
            QGuiApplication.clipboard().setText(ctl.diagnostics())
        actions = (("Reconnect", ctl.restart), ("Restart agent", lambda: ctl.restart_agent("Restarted grok.")),
                   ("Copy diagnostics", copy_diag), ("Sign in again", lambda: ctl.start_login(False)),
                   ("Open data folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(paths.data_dir()))),
                   ("Show log", self._show_log))
        for i, (text, fn) in enumerate(actions):
            b = QPushButton(text)
            b.clicked.connect(fn)
            grid.addWidget(b, i // 2, i % 2)
        g4l.addWidget(info)
        g4l.addLayout(grid)
        g5 = QGroupBox("Voice")
        g5l = QVBoxLayout(g5)
        voice_note = QLabel("Speech recognition and the voice run on this computer.")
        voice_note.setObjectName("muted")
        voice_note.setWordWrap(True)
        g5l.addWidget(voice_note)
        vm = parent.voice if hasattr(parent, "voice") else None
        self.vm = vm
        self.v_enabled = QCheckBox("Show the voice bar (mic button, hands-free, mute)")
        self.v_enabled.setChecked(bool(ctl.settings.get("voice_enabled", True)))
        g5l.addWidget(self.v_enabled)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Speak replies:"))
        self.v_speak = QComboBox()
        for m in SPEAK_MODES:
            self.v_speak.addItem(SPEAK_LABELS[m], m)
        self.v_speak.setCurrentIndex(max(0, SPEAK_MODES.index(ctl.settings.get("voice_speak", "voice"))
                                         if ctl.settings.get("voice_speak", "voice") in SPEAK_MODES else 0))
        r1.addWidget(self.v_speak, 1)
        g5l.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Voice:"))
        self.v_voice = QComboBox()
        for k, info in vmodels.TTS.items():
            have = vmodels.tts_ready(k)
            self.v_voice.addItem(info["label"] + ("" if have else f" — downloads {info['download_mb']} MB"), k)
        cur = ctl.settings.get("voice_voice", vmodels.DEFAULT_TTS)
        self.v_voice.setCurrentIndex(max(0, self.v_voice.findData(cur)))
        r2.addWidget(self.v_voice, 1)
        g5l.addLayout(r2)
        r3 = QHBoxLayout()
        r3.addWidget(QLabel("Speed:"))
        self.v_speed = QSlider(Qt.Horizontal)
        self.v_speed.setRange(60, 180)
        self.v_speed.setValue(int(float(ctl.settings.get("voice_speed", 1.0)) * 100))
        self.v_speed_lbl = QLabel(f"{self.v_speed.value() / 100:.2f}×")
        self.v_speed.valueChanged.connect(lambda v: self.v_speed_lbl.setText(f"{v / 100:.2f}×"))
        r3.addWidget(self.v_speed, 1)
        r3.addWidget(self.v_speed_lbl)
        g5l.addLayout(r3)
        r4 = QHBoxLayout()
        r4.addWidget(QLabel("Hold-to-talk key:"))
        self.v_key = QLineEdit(ctl.settings.get("voice_ptt_key", "Ctrl+Space"))
        self.v_key.setPlaceholderText("e.g. Ctrl+Space or F9 (works while this window is focused)")
        r4.addWidget(self.v_key, 1)
        test = QPushButton("Test voice")
        test.clicked.connect(self._test_voice)
        r4.addWidget(test)
        g5l.addLayout(r4)
        note = QLabel(f"Speech models are downloaded once on first use (about {vmodels.STT[vmodels.DEFAULT_STT]['download_mb'] + 64} MB) "
                      f"into {vmodels.models_dir()}. Code blocks and tool steps are not read aloud.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        g5l.addWidget(note)
        lay.addWidget(g5)
        lay.addWidget(g4)
        lay.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        outer.addWidget(bb)
        ver = QLabel(f"{APP_NAME} {__version__}")
        ver.setObjectName("muted")
        outer.addWidget(ver)
        # word-wrapped labels need height-for-width, otherwise Qt squeezes them to one clipped line
        h = lay.totalHeightForWidth(660)
        inner.setMinimumHeight(h)
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry().height() - 80 if screen else 700
        self.resize(680, min(h + 90, avail))

    def _fill_models(self):
        self.model.clear()
        cur = self.ctl.acp.current_model or self.ctl.settings.get("model", "")
        models = self.ctl.models()
        if not models:
            self.model.addItem("(connect to grok to see models)", "")
        for m in models:
            self.model.addItem(m.get("name") or m["modelId"], m["modelId"])
            if m["modelId"] == cur:
                self.model.setCurrentIndex(self.model.count() - 1)

    def _mode(self, m: str) -> None:
        if m == "all" and not self.ctl.settings.get("allow_all_warned"):
            box = QMessageBox(QMessageBox.Warning, "Allow everything?",
                              ALLOW_ALL_WARNING.format(hotkey=self.ctl.settings["kill_hotkey"]),
                              QMessageBox.Cancel, self)
            yes = box.addButton("Yes, allow everything", QMessageBox.AcceptRole)
            box.setDefaultButton(QMessageBox.Cancel)
            box.setTextFormat(Qt.RichText)
            self.warning_box = box
            box.exec()
            if box.clickedButton() is not yes:
                self.mode_buttons[self.ctl.mode].setChecked(True)
                return
            self.ctl.acknowledge_allow_all()
        self.ctl.set_mode(m)

    def _control(self, on: bool) -> None:
        self.ctl.settings.set("computer_control", on)
        self.ctl.restart()

    def _test_voice(self) -> None:
        if self.vm is None:
            return
        self._save_voice()
        self.vm.set_voice(self.v_voice.currentData())
        self.vm.speak("Hi, this is how I sound.")

    def _save_voice(self) -> None:
        s = self.ctl.settings
        s["voice_enabled"] = self.v_enabled.isChecked()
        s["voice_speak"] = self.v_speak.currentData()
        s["voice_voice"] = self.v_voice.currentData()
        s["voice_speed"] = self.v_speed.value() / 100
        s["voice_ptt_key"] = self.v_key.text().strip() or "Ctrl+Space"

    def _show_log(self) -> None:
        d = QDialog(self)
        d.setWindowTitle("Log")
        d.resize(800, 500)
        l = QVBoxLayout(d)
        t = QPlainTextEdit("\n".join(self.ctl.log[-300:]), readOnly=True)
        t.setObjectName("detail")
        l.addWidget(t)
        d.exec()

    def done(self, r):
        s = self.ctl.settings
        s["tray"] = self.tray.isChecked()
        s["close_to_tray"] = self.close_tray.isChecked()
        s["quick_ask_hotkey"] = self.quick.text().strip()
        self._save_voice()
        s.save()
        if self.autostart is not None:
            from . import linux_integration as li
            li.set_autostart(self.autostart.isChecked())
        super().done(r)


# ================================================================ quick ask ==
class QuickAsk(QWidget):
    asked = Signal(str)

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        frame = QFrame(self)
        frame.setObjectName("composer")
        frame.setStyleSheet(f"#composer {{ border: 2px solid {V}; background: #15131D; border-radius: 16px; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(16, 10, 10, 10)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(28, 28))
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Ask Grok anything…   (Enter to ask, Esc to close)")
        self.edit.setStyleSheet("QLineEdit { background: transparent; border: none; font-size: 15px; }")
        self.edit.returnPressed.connect(self._go)
        go = QPushButton("↑")
        go.setObjectName("send")
        go.clicked.connect(self._go)
        lay.addWidget(logo)
        lay.addWidget(self.edit, 1)
        lay.addWidget(go)
        self.resize(640, 64)

    def _go(self):
        t = self.edit.text().strip()
        if t:
            self.edit.clear()
            self.hide()
            self.asked.emit(t)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)

    def popup(self):
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.move(scr.x() + (scr.width() - self.width()) // 2, scr.y() + scr.height() // 4)
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()


# ============================================================== main window ==
class MainWindow(QMainWindow):
    settingsClosed = Signal()

    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        self.attachments: list[str] = []
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1180, 780)
        self.setAcceptDrops(True)
        self.force_quit = False

        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.setCentralWidget(root)

        # ---- sidebar
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(270)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 14, 12, 12)
        brand_row = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(30, 30))
        brand = QLabel(f"<span style='color:{V}'>Grok</span> <span style='color:{B}'>Desktop</span>")
        brand.setObjectName("brand")
        brand_row.addWidget(logo)
        brand_row.addWidget(brand, 1)
        sl.addLayout(brand_row)
        self.new_btn = QPushButton("+  New chat")
        self.new_btn.setObjectName("primary")
        self.new_btn.clicked.connect(self.new_chat)
        sl.addWidget(self.new_btn)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search chats")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_sidebar)
        sl.addWidget(self.search)
        self.list = QListWidget()
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setWordWrap(False)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        self.list.itemClicked.connect(lambda it: self.ctl.open_chat(it.data(Qt.UserRole)))
        self.list.itemDoubleClicked.connect(lambda it: self.rename_chat(it.data(Qt.UserRole)))
        sl.addWidget(self.list, 1)
        bottom = QHBoxLayout()
        self.status_dot = QFrame()
        self.status_dot.setFixedSize(8, 8)
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.settings_btn = QPushButton()
        self.settings_btn.setIcon(settings_icon(16))
        self.settings_btn.setIconSize(QSize(16, 16))
        self.settings_btn.setFixedSize(28, 28)
        self.settings_btn.setObjectName("ghost")
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.setAccessibleName("Settings")
        self.settings_btn.clicked.connect(self.open_settings)
        self.github_btn = QPushButton("GitHub")
        self.github_btn.setObjectName("ghost")
        self.github_btn.setToolTip("Sign in to GitHub, clone, commit, push, create a repo")
        self.github_btn.clicked.connect(self.open_github)
        bottom.addWidget(self.status_dot, 0, Qt.AlignVCenter)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.github_btn)
        bottom.addWidget(self.settings_btn)
        sl.addLayout(bottom)
        h.addWidget(side)

        # ---- main
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 12)
        ml.setSpacing(8)
        top = QHBoxLayout()
        top.setContentsMargins(20, 10, 16, 0)
        self.title = QLabel("New chat")
        self.title.setObjectName("h1")
        self.mode_lbl = QLabel()
        self.mode_lbl.setObjectName("muted")
        self.model = QComboBox()
        self.model.setMinimumWidth(150)
        self.model.setToolTip("Grok model")
        self.model.activated.connect(self._model_picked)
        top.addWidget(self.title, 1)
        top.addWidget(self.mode_lbl)
        top.addWidget(self.model)
        ml.addLayout(top)

        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 8, 8, 8)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_text.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.banner_text.setOpenExternalLinks(True)
        bl.addWidget(self.banner_text, 1)
        self.banner_btns: list[QPushButton] = []
        self.banner_layout = bl
        wrap = QHBoxLayout()
        wrap.setContentsMargins(20, 0, 20, 0)
        wrap.addWidget(self.banner)
        ml.addLayout(wrap)
        self.banner.hide()

        self.chat = ChatView()
        self.chat.promptChosen.connect(self._use_prompt)
        ml.addWidget(self.chat, 1)

        self.activity = QLabel()
        self.activity.setObjectName("activity")
        self.activity.setTextFormat(Qt.RichText)
        self.activity.setStyleSheet(f"#activity {{ color: {B}; padding: 2px 4px; }}")
        aw = QHBoxLayout()
        aw.setContentsMargins(24, 0, 20, 0)
        aw.addWidget(self.activity)
        ml.addLayout(aw)
        self.activity.hide()
        self._act_timer = QTimer(self, interval=400, timeout=self._tick_activity)
        self._act_timer.start()

        self.perm = PermissionCard()
        self.perm.answered.connect(self._answer_permission)
        pw = QHBoxLayout()
        pw.setContentsMargins(20, 0, 20, 0)
        pw.addWidget(self.perm)
        ml.addLayout(pw)

        self.voice = VoiceManager(ctl, self)
        self.voicebar = self._build_voicebar()
        vw = QHBoxLayout()
        vw.setContentsMargins(20, 0, 20, 0)
        vw.addWidget(self.voicebar)
        ml.addLayout(vw)

        comp = QFrame()
        comp.setObjectName("composer")
        cl = QVBoxLayout(comp)
        cl.setContentsMargins(10, 8, 10, 8)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        self.chips.addStretch()
        self.chips_w = QWidget()
        self.chips_w.setLayout(self.chips)
        self.chips_w.hide()
        cl.addWidget(self.chips_w)
        row = QHBoxLayout()
        self.attach_btn = QPushButton()
        self.attach_btn.setIcon(paperclip_icon(18))
        self.attach_btn.setIconSize(QSize(18, 18))
        self.attach_btn.setFixedSize(36, 36)
        self.attach_btn.setObjectName("ghost")
        self.attach_btn.setToolTip("Attach files or images")
        self.attach_btn.setAccessibleName("Attach files")
        self.attach_btn.clicked.connect(self.pick_files)
        self.input = Composer()
        self.input.submit.connect(self.send)
        self.input.pastedImage.connect(self._paste_image)
        self.input.pastedFiles.connect(self.add_attachments)
        self.send_btn = QPushButton("↑")
        self.send_btn.setObjectName("send")
        self.send_btn.setToolTip("Send (Enter)")
        self.send_btn.clicked.connect(self._send_or_stop)
        row.addWidget(self.attach_btn, 0, Qt.AlignBottom)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn, 0, Qt.AlignBottom)
        cl.addLayout(row)
        cw = QHBoxLayout()
        cw.setContentsMargins(20, 0, 20, 0)
        cw.addWidget(comp)
        ml.addLayout(cw)
        hint = QLabel(f"Grok can make mistakes. Emergency stop for computer control: {ctl.settings['kill_hotkey']}")
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignCenter)
        ml.addWidget(hint)
        h.addWidget(main, 1)

        self._status_timer = QTimer(self, singleShot=True, interval=2500, timeout=self._update_status)

        c = ctl
        c.stateChanged.connect(lambda _s: self._update_state())
        c.conversationsChanged.connect(self.refresh_sidebar)
        c.currentChanged.connect(self._on_current)
        c.messagesChanged.connect(lambda: self.chat.set_messages(c.messages))
        c.permissionChanged.connect(self._show_permission)
        c.modelsChanged.connect(self._fill_models)
        c.loginInfo.connect(self._login_info)
        c.notice.connect(self.statusMessage)
        c.killedChanged.connect(lambda _k: self._update_state())
        c.stuckChanged.connect(lambda _on, _s: self._update_state())
        c.wantShow.connect(self.bring_to_front)
        self.login = ("", "")
        self._wire_voice()
        self.refresh_sidebar()
        self.chat.set_messages([], now=True)
        self._fill_models()
        self._update_state()


    # --------------------------------------------------------------- voice --
    def _build_voicebar(self) -> QFrame:
        f = QFrame()
        f.setObjectName("voicebar")
        f.setStyleSheet(f"#voicebar {{ background: {PANEL_BG}; border: 1px solid {BORDER_C}; border-radius: 12px; }}")
        l = QHBoxLayout(f)
        l.setContentsMargins(10, 4, 10, 4)
        l.setSpacing(8)
        self.orb = Orb(44)
        l.addWidget(self.orb)
        self.voice_label = QLabel()
        self.voice_label.setTextFormat(Qt.RichText)
        self.voice_label.setMinimumWidth(120)
        l.addWidget(self.voice_label, 1)
        self.mic_btn = QPushButton("Hold to talk")
        self.mic_btn.setObjectName("primary")
        self.mic_btn.pressed.connect(self._mic_pressed)
        self.mic_btn.released.connect(self.voice.ptt_end)
        self.hf_btn = QPushButton("Hands-free")
        self.hf_btn.setCheckable(True)
        self.hf_btn.setToolTip("Keep listening: speak, pause, and Grok answers. The mic closes while Grok is speaking.")
        self.hf_btn.clicked.connect(self._hf_clicked)
        self.mute_btn = QPushButton("Mute")
        self.mute_btn.setCheckable(True)
        self.mute_btn.setToolTip("Mute the microphone")
        self.mute_btn.clicked.connect(self.voice.set_muted)
        self.mute_btn.toggled.connect(lambda on: self.mute_btn.setText("Muted" if on else "Mute"))
        self.stop_voice_btn = QPushButton("Stop")
        self.stop_voice_btn.setToolTip("Stop speaking (and skip the rest of this answer)")
        self.stop_voice_btn.clicked.connect(self.voice.stop_speaking)
        self.skip_btn = QPushButton("Skip")
        self.skip_btn.setToolTip("Skip this sentence")
        self.skip_btn.clicked.connect(self.voice.skip_sentence)
        self.voice_more = QPushButton("Options")
        self.voice_more.setObjectName("ghost")
        self.voice_more.setToolTip("Hands-free and mute")
        self.voice_more.clicked.connect(lambda: self._reveal_voice(True))
        self._voice_revealed = False
        for b in (self.mic_btn, self.hf_btn, self.mute_btn, self.skip_btn, self.stop_voice_btn, self.voice_more):
            l.addWidget(b)
        self.stop_voice_btn.hide()
        self.skip_btn.hide()
        self.hf_btn.hide()
        self.mute_btn.hide()
        return f

    def _wire_voice(self) -> None:
        v = self.voice
        self._voice_partial = ""
        self._voice_progress = ""
        v.stateChanged.connect(self._voice_state)
        v.levelChanged.connect(self.orb.set_level)
        v.partial.connect(self._voice_partial_text)
        v.notice.connect(self.statusMessage)
        v.progress.connect(self._voice_progress_text)
        v.mutedChanged.connect(self.mute_btn.setChecked)
        v.handsFreeChanged.connect(self.hf_btn.setChecked)
        QApplication.instance().installEventFilter(self)
        self._ptt_down = False
        self.apply_voice_settings()
        self._voice_state("idle")
        QTimer.singleShot(1500, self.voice.preload)

    def apply_voice_settings(self) -> None:
        self.voicebar.setVisible(self.voice.enabled)
        k = self.ctl.settings.get("voice_ptt_key", "Ctrl+Space")
        self.mic_btn.setToolTip(f"Hold to talk (or hold {k}); release to send")
        self._ptt_seq = QKeySequence(k)
        if not self.voice.enabled:
            self.voice.set_hands_free(False)

    def _ptt_combo(self):
        if self._ptt_seq.count() == 0:
            return None, Qt.NoModifier
        c = self._ptt_seq[0]
        return c.key(), c.keyboardModifiers()

    def eventFilter(self, obj, e):
        t = e.type()
        if t in (QEvent.KeyPress, QEvent.KeyRelease) and self.voice.enabled and self.isActiveWindow() and not e.isAutoRepeat():
            key, mods = self._ptt_combo()
            if key is not None:
                if t == QEvent.KeyPress and e.key() == key and (e.modifiers() & mods) == mods:
                    self._ptt_down = True
                    self._mic_pressed()
                    return True
                if t == QEvent.KeyRelease and self._ptt_down and (e.key() == key or e.key() in (
                        Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta)):
                    self._ptt_down = False
                    self.voice.ptt_end()
                    return True
        elif t == QEvent.KeyPress and e.key() == Qt.Key_Escape and self.voice.state == "speaking" and self.isActiveWindow():
            self.voice.stop_speaking()
        return super().eventFilter(obj, e)

    def _mic_pressed(self) -> None:
        v = self.voice
        if not v.muted and not v.ready() and not v._loading:
            ok, why = v.audio_ok()
            if not ok:
                self.statusMessage(why)
                return
            missing = v.models_missing()
            if missing:
                r = QMessageBox.question(
                    self, "Download voice models?",
                    f"Voice mode runs fully on this computer (no cloud, no account). It needs a one-time download of "
                    f"about {v.download_size_mb()} MB (speech recognition + the voice) from the sherpa-onnx project on GitHub.\n\n"
                    "Download now?")
                if r != QMessageBox.Yes:
                    return
        v.ptt_begin()

    def _hf_clicked(self, on: bool) -> None:
        v = self.voice
        if on and not v.ready() and v.models_missing() and not v._loading:
            r = QMessageBox.question(self, "Download voice models?",
                                     f"Hands-free needs a one-time download of about {v.download_size_mb()} MB "
                                     "(speech recognition + the voice). Download now?")
            if r != QMessageBox.Yes:
                self.hf_btn.setChecked(False)
                return
        v.set_hands_free(on)

    def _voice_partial_text(self, t: str) -> None:
        self._voice_partial = t
        self._voice_state(self.voice.state)

    def _voice_progress_text(self, what: str, pct: int) -> None:
        self._voice_progress = "" if pct < 0 else f"Downloading {what}… {pct}%"
        self._voice_state(self.voice.state)

    def _voice_state(self, s: str) -> None:
        self.orb.set_state(s)
        k = self.ctl.settings.get("voice_ptt_key", "Ctrl+Space")
        txt = {"idle": f"<span style='color:{MUTED}'>Voice ready · hold {k} to talk</span>",
               "listening": f"<b style='color:{V}'>Listening…</b> " + (self._voice_partial or ""),
               "thinking": f"<span style='color:{B}'>Thinking…</span>",
               "speaking": f"<span style='color:{B}'>Speaking…</span> <span style='color:{MUTED}'>(Esc to stop)</span>",
               "muted": f"<span style='color:{MUTED}'>Microphone muted</span>",
               "loading": "Preparing voice… " + (self._voice_progress or ""),
               "error": "<span style='color:#FF7A8A'>Voice error: see status</span>"}.get(s, s)
        if s == "idle" and self.voice.hands_free:
            txt = "Hands-free is on"
        self.voice_label.setText(txt)
        self.stop_voice_btn.setVisible(s == "speaking")
        self.skip_btn.setVisible(s == "speaking")
        self.mic_btn.setEnabled(s != "loading")
        self._sync_voice_extras(s)

    def _reveal_voice(self, on: bool = True) -> None:
        self._voice_revealed = on
        self._sync_voice_extras(self.voice.state)

    def _sync_voice_extras(self, state: str) -> None:
        """Orb and Hold to talk stay up; hands-free and mute appear once voice is in use."""
        if state in ("listening", "speaking", "thinking", "muted"):
            self._voice_revealed = True
        show = self._voice_revealed
        self.hf_btn.setVisible(show)
        self.mute_btn.setVisible(show)
        self.voice_more.setVisible(not show)

    # ------------------------------------------------------------- sidebar --
    def refresh_sidebar(self) -> None:
        q = self.search.text()
        self.list.blockSignals(True)
        self.list.clear()
        for conv in self.ctl.conversations(q):
            it = QListWidgetItem(conv["title"])
            it.setData(Qt.UserRole, conv["id"])
            it.setToolTip(f"{conv['title']}\nlast message {time.strftime('%Y-%m-%d %H:%M', time.localtime(conv['updated']))}")
            self.list.addItem(it)
            if conv["id"] == self.ctl.current:
                it.setSelected(True)
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        if self.ctl.current:
            conv = self.ctl.store.get(self.ctl.current)
            self.title.setText(conv["title"] if conv else "New chat")

    def _on_current(self, cid: str) -> None:
        self.chat.limit = 120
        self.chat._last_html = None
        self.refresh_sidebar()
        if not cid:
            self.title.setText("New chat")
        self.chat.set_messages(self.ctl.messages, now=True)

    def _list_menu(self, pos) -> None:
        it = self.list.itemAt(pos)
        if not it:
            return
        cid = it.data(Qt.UserRole)
        m = QMenu(self)
        m.addAction("Rename", lambda: self.rename_chat(cid))
        m.addAction("Delete", lambda: self.delete_chat(cid))
        m.exec(self.list.mapToGlobal(pos))

    def rename_chat(self, cid: str) -> None:
        conv = self.ctl.store.get(cid)
        if not conv:
            return
        title, ok = QInputDialog.getText(self, "Rename chat", "New name:", text=conv["title"])
        if ok and title.strip():
            self.ctl.rename_chat(cid, title.strip())

    def delete_chat(self, cid: str, confirm: bool = True) -> None:
        conv = self.ctl.store.get(cid)
        if not conv:
            return
        if confirm and QMessageBox.question(self, "Delete chat", f"Delete “{conv['title']}”? This can't be undone.") \
                != QMessageBox.Yes:
            return
        self.ctl.delete_chat(cid)

    def new_chat(self) -> None:
        self.ctl.new_chat()
        self.input.setFocus()

    def _use_prompt(self, text: str) -> None:
        self.input.setPlainText(text)
        self.input.setFocus()
        cursor = self.input.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.input.setTextCursor(cursor)
        self.input._fit()

    # ----------------------------------------------------------- composer --
    def _send_or_stop(self) -> None:
        if self.ctl.busy:
            self.ctl.stop()
        else:
            self.send()

    def send(self) -> None:
        text = self.input.toPlainText()
        if self.ctl.send(text, list(self.attachments)):
            self.input.clear()
            self.clear_attachments()

    def pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Attach files", os.path.expanduser("~"),
                                                "All files (*);;Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp)")
        self.add_attachments(files)

    def add_attachments(self, files: list[str]) -> None:
        for f in files:
            if os.path.isfile(f) and f not in self.attachments:
                if os.path.getsize(f) > 50 * 1024 * 1024:
                    self.statusMessage(f"{os.path.basename(f)} is too big (max 50 MB).")
                    continue
                self.attachments.append(f)
                chip = Chip(f)
                chip.removed.connect(self.remove_attachment)
                self.chips.insertWidget(self.chips.count() - 1, chip)
        self.chips_w.setVisible(bool(self.attachments))

    def remove_attachment(self, path: str) -> None:
        if path in self.attachments:
            self.attachments.remove(path)
        for i in range(self.chips.count()):
            w = self.chips.itemAt(i).widget()
            if isinstance(w, Chip) and w.path == path:
                w.deleteLater()
        self.chips_w.setVisible(bool(self.attachments))

    def clear_attachments(self) -> None:
        for p in list(self.attachments):
            self.remove_attachment(p)

    def _paste_image(self, img: QImage) -> None:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        self.add_attachments([self.ctl.save_pasted_image(bytes(ba))])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasUrls():
            self.add_attachments([u.toLocalFile() for u in md.urls() if u.isLocalFile()])
        elif md.hasImage():
            self._paste_image(md.imageData())
        e.acceptProposedAction()

    # -------------------------------------------------------------- state --
    def statusMessage(self, text: str) -> None:
        self.status.setText(text[:60])
        self.status.setToolTip(text)
        self._status_timer.start()

    def _update_status(self) -> None:
        text = STATE_TEXT.get(self.ctl.state, self.ctl.state)
        color = STATE_COLOR.get(self.ctl.state, "#8C88A3")
        self.status.setText(text)
        self.status.setToolTip("")
        self.status_dot.setStyleSheet(f"background: {color}; border-radius: 4px;")

    def _set_banner(self, text: str, buttons: list[tuple[str, object, str]] = (), kind: str = "") -> None:
        key = (text, tuple(b[0] for b in buttons), kind)
        if key == getattr(self, "_banner_key", None):
            return
        self._banner_key = key
        for b in self.banner_btns:
            self.banner_layout.removeWidget(b)
            b.setParent(None)
            b.deleteLater()
        self.banner_btns = []
        if not text:
            self.banner.hide()
            return
        self.banner_text.setText(text)
        for label, fn, obj in buttons:
            b = QPushButton(label)
            if obj:
                b.setObjectName(obj)
            b.clicked.connect(fn)
            self.banner_layout.addWidget(b)
            self.banner_btns.append(b)
        self.banner.setProperty("kind", kind)
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.banner.show()

    def _update_state(self) -> None:
        s = self.ctl.state
        self._update_status()
        busy = self.ctl.busy
        self.send_btn.setObjectName("stop" if busy else "send")
        self.send_btn.setText("■" if busy else "↑")
        self.send_btn.setToolTip("Stop" if busy else "Send (Enter)")
        self.send_btn.style().unpolish(self.send_btn)
        self.send_btn.style().polish(self.send_btn)
        mode = self.ctl.mode
        self.mode_lbl.setTextFormat(Qt.RichText)
        self.mode_lbl.setText(
            f"<span style='color:{MODE_COLOR.get(mode, MUTED)}'>{MODE_LABEL.get(mode, mode)}</span>")
        c = self.ctl
        if c.control.killed:
            self._set_banner("<b>Emergency stop is on</b>: computer control is halted. Sending a new message "
                             "turns it back on.", [("Resume", c.resume_control, "")], "error")
        elif s == "no_grok":
            self._set_banner("<b>The grok app isn't installed yet.</b> Grok Desktop uses xAI's free <i>grok</i> "
                             "command-line app with your SuperGrok account (no API key). Click Install (opens "
                             + paths.SHELL_NAME + ", no admin needed), then Retry.",
                             [("Install Grok", c.install_grok, "primary"), ("Retry", c.start, "")])
        elif s == "needs_login":
            self._set_banner("<b>Sign in to Grok</b> with your SuperGrok account. Your browser will open.",
                             [("Sign in", lambda: c.start_login(False), "primary"),
                              ("Use a code instead", lambda: c.start_login(True), "")])
        elif s == "logging_in":
            url, code = self.login
            extra = (f"<br>If the browser didn't open: <a href='{url}'>{url}</a>" if url else "") + \
                    (f"<br>Code: <b style='font-size:15px'>{code}</b>" if code else "")
            self._set_banner("Waiting for you to finish signing in in the browser…" + extra,
                             [("Cancel", c.cancel_login, "")])
        elif c.stuck and c.busy:
            self._set_banner(f"<b>Grok seems stuck</b>: nothing has come back from it for {int(time.time() - c.acp.last_traffic)}+ seconds. "
                             "A slow command can look like this too. Stop cancels the answer; Restart agent restarts grok "
                             "and keeps this chat.",
                             [("Stop", c.stop, ""), ("Restart agent", lambda: c.restart_agent("Restarted grok because it stopped responding."), "primary"),
                              ("Copy diagnostics", self.copy_diagnostics, "")], "error")
        elif s == "error":
            self._set_banner("grok stopped unexpectedly.", [("Reconnect", c.restart, "primary")], "error")
        else:
            self._set_banner("")

    def _tick_activity(self) -> None:
        """'Grok is thinking… 0:42' with a pulsing dot while a turn runs (also during long tool phases). Separate from the stuck banner."""
        c = self.ctl
        if not c.busy or c.stuck:
            self.activity.setVisible(False)
            return
        el = int(time.time() - c.acp._turn_t0)
        what = {"thinking": "Grok is thinking…", "writing": "Grok is answering…", "working": "Grok is working: " + (c.phase_label or "a tool"),
                "waiting": "Grok is starting on it…"}.get(c.phase, "Grok is working…")
        if c.perm_queue:
            what = "Waiting for your OK (see the card above)"
        dot = "●" if int(time.time() * 2.5) % 2 == 0 else "○"
        self.activity.setText(f"{dot} {what} &nbsp;<span style='color:{MUTED}'>{el // 60}:{el % 60:02d}</span>")
        self.activity.setVisible(True)

    def copy_diagnostics(self) -> None:
        QGuiApplication.clipboard().setText(self.ctl.diagnostics())
        self.statusMessage("Diagnostics copied")

    def _login_info(self, url: str, code: str) -> None:
        self.login = (url, code)
        if url and self.ctl.state == "logging_in" and not getattr(self, "_opened_login", False):
            self._opened_login = True
        self._update_state()

    def _fill_models(self) -> None:
        self.model.blockSignals(True)
        self.model.clear()
        models = self.ctl.models()
        cur = self.ctl.acp.current_model or self.ctl.settings.get("model", "")
        if not models:
            self.model.addItem(cur or "Grok", cur)
        for m in models:
            self.model.addItem(m.get("name") or m["modelId"], m["modelId"])
            if m["modelId"] == cur:
                self.model.setCurrentIndex(self.model.count() - 1)
        self.model.blockSignals(False)

    def _model_picked(self, _i: int) -> None:
        mid = self.model.currentData()
        if mid and mid != self.ctl.acp.current_model:
            self.ctl.set_model(mid)

    def _show_permission(self) -> None:
        req = self.ctl.current_permission()
        self.perm.show_request(req, max(0, len(self.ctl.perm_queue) - 1))
        if req:
            self.bring_to_front()

    def _answer_permission(self, choice: str) -> None:
        self.ctl.answer_permission(choice)

    def open_github(self) -> None:
        from .github_ui import GitHubDialog
        d = getattr(self, "github_dialog", None)
        if d is None:
            d = self.github_dialog = GitHubDialog(self.ctl, self)
        d.show()
        d.raise_()
        d.activateWindow()
        d.refresh()

    def open_settings(self) -> None:
        d = SettingsDialog(self.ctl, self)
        self.settings_dialog = d
        d.exec()
        self._update_state()
        self.apply_voice_settings()
        self._voice_state(self.voice.state)
        self.settingsClosed.emit()

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        if not (self.ctl.settings.get("close_to_tray") and self.ctl.settings.get("tray") and not self.force_quit):
            self.voice.shutdown()
        if self.ctl.settings.get("close_to_tray") and self.ctl.settings.get("tray") and not self.force_quit:
            e.ignore()
            self.hide()
            return
        super().closeEvent(e)
