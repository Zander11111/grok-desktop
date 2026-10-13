"""Write packaging/linux/icons/hicolor/<n>x<n>/apps/grok-desktop.png (the G ring) from grok_desktop.theme."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from grok_desktop.theme import icon_pixmap  # noqa: E402

for s in (16, 24, 32, 48, 64, 128, 256, 512):
    d = os.path.join(ROOT, "packaging", "linux", "icons", "hicolor", f"{s}x{s}", "apps")
    os.makedirs(d, exist_ok=True)
    icon_pixmap(s).save(os.path.join(d, "grok-desktop.png"), "PNG")
print("icons written")
