"""Near-black theme. Surfaces are black and gray; the violet-to-blue accent (#7C5CFF -> #3D8BFF) is reserved for actions, focus, and the mark."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from .paths import IS_WINDOWS

V, B = "#7C5CFF", "#3D8BFF"
BG, PANEL, PANEL2, BORDER, FG, MUTED = "#0C0C0C", "#141414", "#1C1C1C", "#2E2E2E", "#E8E8E8", "#8D8D8D"
HOVER, SELECTED = "#242424", "#2A2A2A"
GRAD = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {V}, stop:1 {B})"


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, c in ((QPalette.Window, BG), (QPalette.WindowText, FG), (QPalette.Base, BG),
                    (QPalette.AlternateBase, PANEL2), (QPalette.Text, FG), (QPalette.Button, PANEL2),
                    (QPalette.ButtonText, FG), (QPalette.Highlight, V), (QPalette.HighlightedText, "#FFFFFF"),
                    (QPalette.ToolTipBase, PANEL2), (QPalette.ToolTipText, FG), (QPalette.Link, "#A996FF"),
                    (QPalette.PlaceholderText, "#6A6A6A")):
        pal.setColor(role, QColor(c))
    app.setPalette(pal)
    f = QFont("Segoe UI" if IS_WINDOWS else "DejaVu Sans")
    f.setPointSize(10)
    app.setFont(f)
    app.setStyleSheet(f"""
        QWidget {{ color: {FG}; }}
        QToolTip {{ background: {PANEL2}; color: {FG}; border: 1px solid {BORDER}; }}
        #sidebar {{ background: {PANEL}; border-right: 1px solid {BORDER}; }}
        #brand {{ font-size: 17px; font-weight: bold; }}
        QPushButton {{ padding: 7px 14px; border-radius: 8px; border: 1px solid {BORDER}; background: {PANEL2}; }}
        QPushButton:hover {{ border-color: {V}; }}
        QPushButton:disabled {{ color: #5A5A5A; }}
        QPushButton#primary {{ background: {GRAD}; border: none; color: white; font-weight: bold; }}
        QPushButton#primary:disabled {{ background: #2A2A2A; color: #7A7A7A; }}
        QPushButton#danger {{ background: #5A1E2A; border-color: #8A2A3C; color: white; font-weight: bold; }}
        QPushButton#ghost {{ background: transparent; border: none; padding: 6px; }}
        QPushButton#ghost:hover {{ background: {PANEL2}; }}
        QPushButton#send {{ background: {GRAD}; border: none; color: white; font-weight: bold; border-radius: 18px;
                            min-width: 36px; min-height: 36px; max-width: 36px; max-height: 36px; padding: 0; font-size: 16px; }}
        QPushButton#stop {{ background: #E5484D; border: none; color: white; border-radius: 18px; font-size: 14px;
                            min-width: 36px; min-height: 36px; max-width: 36px; max-height: 36px; padding: 0; }}
        QLineEdit, QPlainTextEdit, QComboBox, QKeySequenceEdit {{ background: {PANEL2}; border: 1px solid {BORDER};
                            border-radius: 8px; padding: 6px 8px; selection-background-color: {V}; }}
        QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {V}; }}
        QComboBox QAbstractItemView {{ background: {PANEL2}; selection-background-color: {SELECTED}; }}
        QListWidget {{ background: transparent; border: none; outline: none; }}
        QListWidget::item {{ padding: 8px 10px; border-radius: 8px; margin: 1px 0; }}
        QListWidget::item:hover {{ background: {HOVER}; }}
        QListWidget::item:selected {{ background: {SELECTED}; color: white; }}
        QTextBrowser#chat {{ background: {BG}; border: none; }}
        #composer {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 14px; }}
        #composer QPlainTextEdit {{ background: transparent; border: none; padding: 4px; }}
        #chip {{ background: #222222; border: 1px solid {BORDER}; border-radius: 8px; }}
        #permCard {{ background: #161616; border: 1px solid {V}; border-radius: 12px; }}
        #permCard[risk="high"] {{ border-color: #E5484D; }}
        #banner {{ background: #161616; border: 1px solid {BORDER}; border-radius: 10px; }}
        #banner[kind="error"] {{ background: #3A1F2A; border-color: #8A2A3C; }}
        #detail {{ background: #0A0A0A; border: 1px solid {BORDER}; border-radius: 8px;
                   font-family: 'Cascadia Mono', Consolas, 'DejaVu Sans Mono', monospace; }}
        #muted {{ color: {MUTED}; }}
        #hint {{ color: {MUTED}; font-size: 11px; }}
        #h1 {{ font-size: 18px; font-weight: bold; }}
        QRadioButton, QCheckBox {{ spacing: 8px; }}
        QRadioButton::indicator {{ width: 16px; height: 16px; border: 2px solid #5A5A5A; border-radius: 8px; background: transparent; }}
        QRadioButton::indicator:checked {{ background: {V}; border: 2px solid {V}; border-radius: 8px; }}
        QCheckBox::indicator {{ width: 16px; height: 16px; border: 2px solid #5A5A5A; border-radius: 4px; background: transparent; }}
        QCheckBox::indicator:checked {{ background: {V}; border: 2px solid {V}; border-radius: 4px; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; }}
        QScrollBar::handle:vertical {{ background: #3A3A3A; border-radius: 4px; min-height: 30px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
        QMenu {{ background: {PANEL2}; border: 1px solid {BORDER}; padding: 4px; }}
        QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
        QMenu::item:selected {{ background: {SELECTED}; }}
        QDialog {{ background: {BG}; }}
        QGroupBox {{ border: 1px solid {BORDER}; border-radius: 10px; margin-top: 14px; padding: 10px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {FG}; font-weight: bold; }}
    """)


def icon_pixmap(size: int = 256) -> QPixmap:
    """The "G ring" mark: a violet->blue ring with a gap, on a dark rounded square."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(0, 0, size, size)
    bg = QPainterPath()
    bg.addRoundedRect(r.adjusted(size * .02, size * .02, -size * .02, -size * .02), size * .22, size * .22)
    p.fillPath(bg, QColor("#121212"))
    g = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    g.setColorAt(0, QColor(V))
    g.setColorAt(1, QColor(B))
    pen = QPen(g, size * .13, Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    ring = r.adjusted(size * .22, size * .22, -size * .22, -size * .22)
    p.drawArc(ring, 30 * 16, 290 * 16)
    p.drawLine(QPointF(size * .5, size * .5), QPointF(size * .74, size * .5))
    p.end()
    return pm


def _line_icon(draw, size: int = 18, color: str = "#C8C8C8") -> QIcon:
    """A small monochrome stroke icon. Emoji glyphs are missing or monochrome on some Linux fonts."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), max(1.35, size / 12.0), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    draw(p, float(size))
    p.end()
    return QIcon(pm)


def paperclip_icon(size: int = 18) -> QIcon:
    def draw(p: QPainter, s: float) -> None:
        # Open loops so the mark still reads as a clip at toolbar size.
        pen = p.pen()
        pen.setWidthF(max(1.15, s / 14.0))
        p.setPen(pen)
        path = QPainterPath()
        left, right = s * 0.22, s * 0.78
        inner_l, inner_r = s * 0.42, s * 0.58
        top, bot = s * 0.12, s * 0.88
        path.moveTo(inner_r, s * 0.40)
        path.lineTo(inner_r, s * 0.62)
        path.cubicTo(inner_r, bot - s * 0.10, inner_l, bot - s * 0.10, inner_l, s * 0.62)
        path.lineTo(inner_l, s * 0.42)
        path.cubicTo(inner_l, top + s * 0.10, right, top + s * 0.06, right, s * 0.36)
        path.lineTo(right, s * 0.64)
        path.cubicTo(right, bot, left, bot, left, s * 0.64)
        path.lineTo(left, s * 0.30)
        path.cubicTo(left, top, inner_r + s * 0.02, top, inner_r + s * 0.02, s * 0.32)
        p.drawPath(path)
    return _line_icon(draw, size)


def settings_icon(size: int = 18) -> QIcon:
    """Slider rows, which stay recognizable at the size of a toolbar button."""
    def draw(p: QPainter, s: float) -> None:
        color = QColor("#C8C8C8")
        pen = p.pen()
        for y, knob in ((0.24, 0.64), (0.50, 0.38), (0.76, 0.70)):
            cy, kx = s * y, s * knob
            radius = s * 0.13
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawLine(QPointF(s * 0.14, cy), QPointF(kx - radius - 0.4, cy))
            p.drawLine(QPointF(kx + radius + 0.4, cy), QPointF(s * 0.86, cy))
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(kx, cy), radius, radius)
    return _line_icon(draw, size)


def file_icon(size: int = 18) -> QIcon:
    def draw(p: QPainter, s: float) -> None:
        p.drawRoundedRect(QRectF(s * 0.24, s * 0.12, s * 0.46, s * 0.76), 1.6, 1.6)
        p.drawLine(QPointF(s * 0.34, s * 0.40), QPointF(s * 0.60, s * 0.40))
        p.drawLine(QPointF(s * 0.34, s * 0.54), QPointF(s * 0.60, s * 0.54))
        p.drawLine(QPointF(s * 0.34, s * 0.68), QPointF(s * 0.52, s * 0.68))
    return _line_icon(draw, size)


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(icon_pixmap(s))
    return ic
