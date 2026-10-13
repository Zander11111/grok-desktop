"""Write packaging/icon.ico (PNG-in-ICO, 16-256 px) from the icon drawn in grok_desktop.theme."""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from grok_desktop.theme import icon_pixmap  # noqa: E402

sizes = [16, 24, 32, 48, 64, 128, 256]
pngs = []
for s in sizes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    icon_pixmap(s).toImage().save(buf, "PNG")
    pngs.append(bytes(ba))
head = struct.pack("<HHH", 0, 1, len(sizes))
off = 6 + 16 * len(sizes)
entries = b""
for s, data in zip(sizes, pngs):
    entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), off)
    off += len(data)
out = os.path.join(ROOT, "packaging", "icon.ico")
with open(out, "wb") as f:
    f.write(head + entries + b"".join(pngs))
icon_pixmap(256).save(os.path.join(ROOT, "packaging", "icon.png"))
print(out, os.path.getsize(out))
