"""Scroll-corruption check against the REAL screen (Xvfb root capture, not widget.grab()): scroll with wheel/scrollbar steps,
capture the chat area, then force a full repaint and capture again; the two must match. usage: scroll_repro.py <src-dir> <out-dir>"""
import os, sys, subprocess, tempfile
src, out = sys.argv[1], sys.argv[2]
sys.path.insert(0, src); os.makedirs(out, exist_ok=True)
os.environ.setdefault("HOME", tempfile.mkdtemp())
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QImage, QWheelEvent
from grok_desktop import theme, paths
from grok_desktop.controller import Controller
from grok_desktop.ui import MainWindow
qa = QApplication(sys.argv[:1]); theme.apply(qa)
os.environ["GD_HOME"] = tempfile.mkdtemp()
ctl = Controller(paths.Settings())
w = MainWindow(ctl); w.resize(1000, 700); w.show()
def pump(t=0.3):
    import time; e = time.time() + t
    while time.time() < e: QCoreApplication.processEvents(); time.sleep(0.005)
msgs = []
for i in range(int(os.environ.get("NMSG","120"))):
    msgs.append({"id": i*3, "role": "user", "text": f"user message {i}: it should also expand smoothly when the mouse hovers over it", "attachments": [], "meta": {}})
    msgs.append({"id": i*3+1, "role": "assistant", "text": f"Reply {i}. The test notch sat on top of your screen, so the overlay approach works. " * 4 + "\n\n```python\nprint(1)\n```", "attachments": [], "meta": {}})
    msgs.append({"id": i*3+2, "role": "tool", "text": f"Run a command {i}", "attachments": [], "meta": {"status": "completed"}})
w.chat.messages = msgs; w.chat._render(); pump(0.8)
vp = w.chat.viewport(); sb = w.chat.verticalScrollBar()
def capture(name):
    pump(0.3)
    g = vp.mapToGlobal(QPoint(0, 0)); r = vp.rect()
    p = os.path.join(out, name)
    subprocess.run(["import", "-window", "root", "-crop", f"{r.width()}x{r.height()}+{g.x()}+{g.y()}", "+repage", p], check=True)
    return QImage(p)
def diff(a, b):
    n = 0
    for y in range(0, a.height(), 2):
        for x in range(0, a.width(), 2):
            if a.pixel(x, y) != b.pixel(x, y): n += 1
    return n / ((a.height() // 2) * (a.width() // 2))
worst = 0
sb.setValue(sb.maximum() // 2); pump(0.3)
steps = [120, 120, -60, 37, 300, -500, 15, 15, 15, 1000, -1000]
for i, d in enumerate(steps):
    ev = QWheelEvent(QPoint(300, 300), vp.mapToGlobal(QPoint(300, 300)), QPoint(0, 0), QPoint(0, -d), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(vp, ev); sb.setValue(sb.value() + d // 2); pump(0.05)
    a = capture(f"s{i}-scrolled.png")
    vp.update(); w.chat.update(); vp.repaint(); pump(0.2)
    b = capture(f"s{i}-fresh.png")
    f = diff(a, b); worst = max(worst, f); print(i, d, f"{f:.4f}")
print("WORST", worst)
os._exit(0 if worst < 0.002 else 1)
