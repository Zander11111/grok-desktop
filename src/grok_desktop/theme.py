"""Dark theme with the Grid OS violet-to-blue accent (#7C5CFF -> #3D8BFF), and the app icon (drawn in code)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from .paths import IS_WINDOWS

V, B = "#7C5CFF", "#3D8BFF"
BG, PANEL, PANEL2, BORDER, FG, MUTED = "#0F0E15", "#15131D", "#1C1A26", "#2A2738", "#E8E6F0", "#8C88A3"
GRAD = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {V}, stop:1 {B})"


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, c in ((QPalette.Window, BG), (QPalette.WindowText, FG), (QPalette.Base, BG),
                    (QPalette.AlternateBase, PANEL2), (QPalette.Text, FG), (QPalette.Button, PANEL2),
                    (QPalette.ButtonText, FG), (QPalette.Highlight, V), (QPalette.HighlightedText, "#FFFFFF"),
                    (QPalette.ToolTipBase, PANEL2), (QPalette.ToolTipText, FG), (QPalette.Link, "#A996FF"),
                    (QPalette.PlaceholderText, "#6F6B85")):
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
        QPushButton:disabled {{ color: #5E5A72; }}
        QPushButton#primary {{ background: {GRAD}; border: none; color: white; font-weight: bold; }}
        QPushButton#primary:disabled {{ background: #2E2A45; color: #8A86A0; }}
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
        QComboBox QAbstractItemView {{ background: {PANEL2}; selection-background-color: #2D2650; }}
        QListWidget {{ background: transparent; border: none; outline: none; }}
        QListWidget::item {{ padding: 8px 10px; border-radius: 8px; margin: 1px 0; }}
        QListWidget::item:hover {{ background: #1F1C2B; }}
        QListWidget::item:selected {{ background: #2D2650; color: white; }}
        QTextBrowser#chat {{ background: {BG}; border: none; }}
        #composer {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 14px; }}
        #composer QPlainTextEdit {{ background: transparent; border: none; padding: 4px; }}
        #chip {{ background: #262337; border: 1px solid {BORDER}; border-radius: 8px; }}
        #permCard {{ background: #1A1726; border: 1px solid {V}; border-radius: 12px; }}
        #permCard[risk="high"] {{ border-color: #E5484D; }}
        #banner {{ background: #221E33; border: 1px solid {BORDER}; border-radius: 10px; }}
        #banner[kind="error"] {{ background: #3A1F2A; border-color: #8A2A3C; }}
        #detail {{ background: #0B0A10; border: 1px solid {BORDER}; border-radius: 8px;
                   font-family: 'Cascadia Mono', Consolas, 'DejaVu Sans Mono', monospace; }}
        #muted {{ color: {MUTED}; }}
        #h1 {{ font-size: 18px; font-weight: bold; }}
        QRadioButton, QCheckBox {{ spacing: 8px; }}
        QRadioButton::indicator:checked, QCheckBox::indicator:checked {{ background: {V}; border: 2px solid {V}; border-radius: 7px; }}
        QRadioButton::indicator, QCheckBox::indicator {{ width: 12px; height: 12px; border: 2px solid #5E5A72; border-radius: 7px; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; }}
        QScrollBar::handle:vertical {{ background: #3A3650; border-radius: 4px; min-height: 30px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
        QMenu {{ background: {PANEL2}; border: 1px solid {BORDER}; padding: 4px; }}
        QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
        QMenu::item:selected {{ background: #2D2650; }}
        QDialog {{ background: {BG}; }}
        QGroupBox {{ border: 1px solid {BORDER}; border-radius: 10px; margin-top: 14px; padding: 10px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #B7A8FF; font-weight: bold; }}
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
    p.fillPath(bg, QColor("#14121C"))
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


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(icon_pixmap(s))
    return ic
