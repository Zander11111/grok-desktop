"""Small animated orb: violet #7C5CFF -> blue #3D8BFF. idle = slow breathing, listening = reacts to the mic, thinking = orbiting
dots, speaking = rippling waves, muted = grey. No assets, drawn in code."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

VIOLET, BLUE = QColor("#7C5CFF"), QColor("#3D8BFF")


def mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


class Orb(QWidget):
    def __init__(self, size: int = 48, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.state = "idle"
        self.level = 0.0
        self._smooth = 0.0
        self._t0 = time.time()
        self._timer = QTimer(self, interval=33, timeout=self._tick)
        self.setToolTip("Voice status")

    def set_state(self, s: str) -> None:
        self.state = s
        self.update()

    def set_level(self, v: float) -> None:
        self.level = max(0.0, min(1.0, v))

    def showEvent(self, e):
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self) -> None:
        target = self.level if self.state == "listening" else 0.0
        self._smooth += (target - self._smooth) * 0.35
        self.level *= 0.85
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = time.time() - self._t0
        w = self.width()
        c = QPointF(w / 2, w / 2)
        base = w * 0.27
        s = self.state
        if s == "muted":
            a, b = QColor("#5A5A5A"), QColor("#3A3A3A")
            r = base
        else:
            a, b = VIOLET, BLUE
            if s == "listening":
                r = base * (1.0 + 0.45 * self._smooth) + 1.5 * math.sin(t * 5)
            elif s == "speaking":
                r = base * (1.0 + 0.16 * math.sin(t * 9) + 0.10 * math.sin(t * 14.3 + 1))
            elif s == "thinking":
                r = base * (0.92 + 0.05 * math.sin(t * 4))
            elif s == "loading":
                r = base * (0.9 + 0.05 * math.sin(t * 3))
            else:
                r = base * (0.9 + 0.06 * math.sin(t * 1.6))
        phase = (math.sin(t * (3 if s != "idle" else 1)) + 1) / 2
        # glow
        if s != "muted":
            glow = QRadialGradient(c, w * 0.5)
            col = mix(a, b, phase)
            col.setAlpha(110 if s != "idle" else 45)
            glow.setColorAt(0.35, col)
            col2 = QColor(col)
            col2.setAlpha(0)
            glow.setColorAt(1.0, col2)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(glow))
            p.drawEllipse(c, w * 0.5, w * 0.5)
        # speaking / listening ripples
        if s in ("speaking", "listening") and s != "muted":
            for i in range(2):
                k = ((t * (0.9 if s == "speaking" else 0.6) + i * 0.5) % 1.0)
                col = mix(a, b, k)
                col.setAlpha(int(150 * (1 - k)))
                p.setPen(QPen(col, 1.6))
                p.setBrush(Qt.NoBrush)
                rr = r + k * (w * 0.5 - r - 1)
                p.drawEllipse(c, rr, rr)
        # body
        g = QRadialGradient(QPointF(c.x() - r * 0.35, c.y() - r * 0.4), r * 1.6)
        g.setColorAt(0.0, mix(a, QColor("#FFFFFF"), 0.35))
        g.setColorAt(0.45, a)
        g.setColorAt(1.0, b)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(c, r, r)
        # thinking: orbiting dots
        if s == "thinking":
            for i in range(3):
                ang = t * 3.2 + i * 2.094
                q = QPointF(c.x() + math.cos(ang) * (r + 6), c.y() + math.sin(ang) * (r + 6))
                p.setBrush(mix(a, b, i / 2))
                p.drawEllipse(q, 2.6, 2.6)
        if s == "muted":
            p.setPen(QPen(QColor("#C8C8C8"), 2))
            p.drawLine(QPointF(c.x() - r, c.y() + r), QPointF(c.x() + r, c.y() - r))
        p.end()
