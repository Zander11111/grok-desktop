"""On-screen indicator while the AI controls the mouse/keyboard: a glowing violet->blue frame around the screen
(click-through) and a banner at the top with what it's doing and a big Stop button (= emergency stop).
On Windows both windows are excluded from screen capture (SetWindowDisplayAffinity WDA_EXCLUDEFROMCAPTURE), so
grok's own screenshots don't show them."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from .paths import IS_WINDOWS
from .theme import B, V


def exclude_from_capture(w: QWidget) -> None:
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.user32.SetWindowDisplayAffinity(int(w.winId()), 0x11)  # WDA_EXCLUDEFROMCAPTURE (Win10 2004+)
    except Exception:  # noqa: BLE001
        pass


class _Frame(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.phase = 0.0

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, self.width(), self.height())
        a = 170 + int(60 * abs(1 - 2 * (self.phase % 1.0)))
        c1, c2 = QColor(V), QColor(B)
        c1.setAlpha(a)
        c2.setAlpha(a)
        g.setColorAt(0, c1)
        g.setColorAt(1, c2)
        for width, alpha in ((14, 40), (8, 90), (4, 255)):
            pen = QPen(g, width)
            col = QColor(V)
            col.setAlpha(alpha)
            p.setOpacity(alpha / 255)
            p.setPen(pen)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(width / 2, width / 2, -width / 2, -width / 2), 10, 10)


class Banner(QWidget):
    stopClicked = Signal()

    def __init__(self, hotkey: str):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 10, 10, 10)
        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color: {V}; font-size: 18px;")
        self.text = QLabel("Grok is controlling your mouse and keyboard")
        self.text.setStyleSheet("color: white; font-weight: bold;")
        self.sub = QLabel("")
        self.sub.setStyleSheet("color: #BDBDBD;")
        self.stop = QPushButton(f"■  Stop   ({hotkey})")
        self.stop.setObjectName("danger")
        self.stop.setCursor(Qt.PointingHandCursor)
        self.stop.clicked.connect(self.stopClicked)
        lay.addWidget(self.dot)
        lay.addWidget(self.text)
        lay.addWidget(self.sub, 1)
        lay.addWidget(self.stop)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, self.width(), 0)
        g.setColorAt(0, QColor(16, 16, 16, 242))
        g.setColorAt(1, QColor(24, 24, 24, 242))
        p.setBrush(g)
        p.setPen(QPen(QColor(V), 1.5))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 14, 14)


class ControlIndicator:
    def __init__(self, hotkey: str = "Ctrl+Alt+Esc"):
        self.frame = _Frame()
        self.banner = Banner(hotkey)
        self.stopClicked = self.banner.stopClicked
        self._t = QTimer(interval=60)
        self._t.timeout.connect(self._tick)
        self.visible = False

    def _tick(self):
        self.frame.phase += 0.03
        self.frame.update()

    def show(self, what: str = "") -> None:
        scr = QGuiApplication.primaryScreen().geometry()
        self.frame.setGeometry(scr)
        self.banner.sub.setText(("· " + what) if what else "")
        self.banner.adjustSize()
        w = max(560, min(self.banner.sizeHint().width() + 40, scr.width() - 40))
        self.banner.setGeometry(scr.x() + (scr.width() - w) // 2, scr.y() + 16, w, 58)
        if not self.visible:
            self.frame.show()
            self.banner.show()
            exclude_from_capture(self.frame)
            exclude_from_capture(self.banner)
            self._t.start()
        self.frame.raise_()
        self.banner.raise_()
        self.visible = True

    def hide(self) -> None:
        self._t.stop()
        self.frame.hide()
        self.banner.hide()
        self.visible = False

    def set_active(self, on: bool, what: str = "") -> None:
        self.show(what) if on else self.hide()
