"""Render the README screenshots offscreen against the fake grok.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/screenshot_run.py [outdir]

Chat content in the history shots is seeded into the real SQLite store; the permission prompt comes from a
real (fake-grok) turn going through the real MCP server. The indicator shot is composited from the real
overlay widgets over a mock desktop, since the offscreen platform has no desktop to capture.
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(ROOT, "src"), HERE]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["HOME"] = tempfile.mkdtemp(prefix="gd-shots-")

from pathlib import Path  # noqa: E402

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen,  # noqa: E402
                           QPixmap, QPolygon)
from PySide6.QtWidgets import QWidget  # noqa: E402

from helpers import app, fake_env, pump, wait_until  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "screenshots"))
OUT.mkdir(parents=True, exist_ok=True)
tmp = Path(tempfile.mkdtemp(prefix="gd-shots-"))
qa = app()
fake_env(tmp, speed="200")

from grok_desktop import paths, theme  # noqa: E402
from grok_desktop.app import DesktopApp  # noqa: E402
from grok_desktop.ui import SettingsDialog  # noqa: E402

theme.apply(qa)
paths.Settings().set("tray", False)
d = DesktopApp(qa, show=True)
w, ctl, st = d.win, d.ctl, d.ctl.store
w.resize(1280, 800)
assert wait_until(lambda: ctl.state == "ready")


def save(pix: QPixmap, name: str):
    pix.save(str(OUT / name))
    print("saved", OUT / name, pix.width(), "x", pix.height())


def shot(name: str):
    pump(0.3)
    save(w.grab(), name)


def dim_under(base: QPixmap, top: QPixmap) -> QPixmap:
    out = QPixmap(base)
    p = QPainter(out)
    p.fillRect(out.rect(), QColor(0, 0, 0, 120))
    p.drawPixmap((out.width() - top.width()) // 2, (out.height() - top.height()) // 2, top)
    p.end()
    return out


# ---- seed history ------------------------------------------------------------------------------
def seed(title, msgs, age):
    cid = st.create(title)
    for role, text, *rest in msgs:
        st.add(cid, role, text, attachments=(rest[0] if rest else None), meta=(rest[1] if len(rest) > 1 else None))
    st.db.execute("UPDATE conversations SET updated=? WHERE id=?", (time.time() - age, cid))
    st.db.commit()
    return cid


seed("Minecraft server won't start", [("user", "my minecraft server says port 25565 in use"),
                                      ("assistant", "Something else is using it. Run `netstat -ano | findstr 25565`.")], 86400 * 6)
seed("Blog post outline", [("user", "outline a blog post about home servers"), ("assistant", "**Thesis:** ...")], 86400 * 4)
seed("Clean up my Downloads folder", [("user", "sort my downloads by type"), ("assistant", "Done: moved 214 files.")], 86400 * 2)
seed("Quadratic formula refresher", [("user", "explain the quadratic formula"), ("assistant", "x = (−b ± √(b²−4ac)) / 2a")], 86400)
main = seed("Python: count words in a file", [
    ("user", "Write a Python script that counts the most common words in a text file"),
    ("thought", "They want a short script, use collections.Counter and argparse."),
    ("assistant", "Here's a small script that uses `collections.Counter`:\n\n```python\nimport re\nimport sys\n"
                  "from collections import Counter\n\n\ndef top_words(path, n=10):\n    with open(path, encoding=\"utf-8\") as f:\n"
                  "        words = re.findall(r\"[a-z']+\", f.read().lower())\n    return Counter(words).most_common(n)\n\n\n"
                  "if __name__ == \"__main__\":\n    for word, count in top_words(sys.argv[1]):\n        print(f\"{count:5}  {word}\")\n```\n\n"
                  "Run it with:\n\n```powershell\npython top_words.py .\\essay.txt\n```\n\n"
                  "**How it works**\n- `re.findall` grabs runs of letters (and apostrophes, so *don't* stays one word)\n"
                  "- `Counter.most_common(n)` sorts by frequency for you"),
    ("user", "can you run it on my essay.txt in Documents?"),
    ("tool", "Search files: essay.txt in C:\\Users\\user\\Documents", None, {"status": "completed"}),
    ("tool", "Run PowerShell: python top_words.py \"C:\\Users\\user\\Documents\\essay.txt\"", None, {"status": "completed"}),
    ("assistant", "Top words in **essay.txt** (1,284 words total):\n\n| count | word |\n|---:|---|\n| 71 | the |\n| 38 | gatsby |\n"
                  "| 29 | daisy |\n| 22 | green |\n| 17 | light |\n\nWant me to skip filler words like *the* and *and*?"),
], 60)
ctl.conversationsChanged.emit()
ctl.open_chat(main)
w.refresh_sidebar()
shot("01-chat-history.png")

# ---- attachment ---------------------------------------------------------------------------------
img = QImage(640, 380, QImage.Format_RGB32)
img.fill(QColor("#0E1220"))
p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)
p.setPen(QPen(QColor("#2A3050"), 1))
for x in range(60, 640, 60):
    p.drawLine(x, 30, x, 330)
pts = [(60, 300), (120, 270), (180, 280), (240, 210), (300, 220), (360, 160), (420, 170), (480, 110), (540, 90), (600, 60)]
path = QPainterPath(QPoint(*pts[0]))
for pt in pts[1:]:
    path.lineTo(*pt)
g = QLinearGradient(0, 0, 640, 0)
g.setColorAt(0, QColor("#7C5CFF"))
g.setColorAt(1, QColor("#3D8BFF"))
p.setPen(QPen(g, 5))
p.drawPath(path)
p.setPen(QColor("#E8E6F0"))
p.setFont(QFont("DejaVu Sans", 18, QFont.Bold))
p.drawText(60, 24, "Lab 4: cooling curve (°C vs min)")
p.end()
chart = tmp / "lab4-graph.png"
img.save(str(chart))
notes = tmp / "lab4-notes.txt"
notes.write_text("Trial 1: 92, 81, 72, 66, 61\nTrial 2: 93, 80, 71, 64, 60\n")
att = [ctl.prepare_attachment(str(chart)), ctl.prepare_attachment(str(notes))]
cid = seed("Chem lab graph check", [
    ("user", "Does my graph match my data? Is the trend right?", att),
    ("tool", "View image: lab4-graph.png", None, {"status": "completed"}),
    ("assistant", "Your graph goes **up**, but your notes show temperature going **down** (92 → 61 °C). "
                  "It looks like the y-axis is flipped.\n\nFix: plot minutes on *x*, °C on *y*, highest value at the top. "
                  "Both trials then show the expected cooling curve that levels off near room temperature."),
], 30)
ctl.open_chat(cid)
w.refresh_sidebar()
shot_png = tmp / "screenshot-2026-10-02.png"
img.scaled(320, 190).save(str(shot_png))
w.add_attachments([str(shot_png), str(notes)])
w.input.setPlainText("Here's the fixed one, better?")
shot("02-attachment.png")
w.clear_attachments()
w.input.clear()

# ---- permission prompt (real turn through the fake grok + MCP server) ------------------------------
w.new_chat()
w.input.setPlainText("delete C:\\Users\\user\\Documents\\old-report.docx")
w.send()
assert wait_until(lambda: w.perm.isVisibleTo(w), 20), ctl.log[-20:]
shot("03-permission-prompt.png")
w.perm.deny.click()
assert wait_until(lambda: not ctl.busy, 20)
w.delete_chat(ctl.current, confirm=False)

# ---- settings + allow-everything warning -----------------------------------------------------------
ctl.open_chat(main)
pump(0.2)
base = w.grab()
sd = SettingsDialog(ctl, w)
sd.show()
pump(0.3)
save(dim_under(base, sd.grab()), "04-settings.png")
grabbed = {}


def grab_warning():
    box = sd.warning_box
    pump(0.2)
    grabbed["pix"] = dim_under(dim_under(base, sd.grab()), box.grab())
    box.reject()


QTimer.singleShot(300, grab_warning)
sd.mode_buttons["all"].setChecked(True)
pump(0.2)
save(grabbed["pix"], "05-allow-everything-warning.png")
sd.close()

# ---- computer-control indicator, composited over a mock desktop ----------------------------------------
cid = seed("Type my notes into Notepad", [
    ("user", "open notepad and type my lab notes in it"),
    ("tool", "Open app: notepad", None, {"status": "completed"}),
    ("tool", "Screenshot: whole screen", None, {"status": "completed"}),
    ("tool", "Click at 412, 300", None, {"status": "completed"}),
    ("tool", "Type text: Trial 1: 92, 81, 72, 66, 61…", None, {"status": "in_progress"}),
], 5)
ctl.open_chat(cid)
w.refresh_sidebar()
W, H = 1600, 900
desk = QPixmap(W, H)
p = QPainter(desk)
p.setRenderHint(QPainter.Antialiasing)
bg = QLinearGradient(0, 0, W, H)
bg.setColorAt(0, QColor("#1B1340"))
bg.setColorAt(1, QColor("#0B2A55"))
p.fillRect(desk.rect(), bg)
p.fillRect(QRect(0, H - 44, W, 44), QColor(20, 20, 28, 235))                 # taskbar
p.setPen(QColor("#E8E6F0"))
p.drawText(QRect(W - 120, H - 44, 110, 44), Qt.AlignCenter, "11:02 PM")
# a Notepad-ish window being typed into
np_r = QRect(60, 110, 560, 430)
p.fillRect(np_r, QColor("#FAFAFA"))
p.fillRect(QRect(np_r.x(), np_r.y(), np_r.width(), 34), QColor("#E9E9EE"))
p.setPen(QColor("#222"))
p.setFont(QFont("DejaVu Sans", 10))
p.drawText(np_r.adjusted(12, 0, 0, 0).topLeft() + QPoint(0, 22), "*Untitled - Notepad")
p.setFont(QFont("DejaVu Sans Mono", 11))
p.drawText(np_r.topLeft() + QPoint(14, 64), "Trial 1: 92, 81, 72, 66, 61")
p.drawText(np_r.topLeft() + QPoint(14, 86), "Trial 2: 93, 80, 7|")
w.resize(820, 700)
pump(0.3)
wp = w.grab()
p.drawPixmap(W - wp.width() - 40, 100, wp)
cur = QPolygon([QPoint(0, 0), QPoint(0, 22), QPoint(6, 17), QPoint(10, 26), QPoint(14, 24), QPoint(10, 15), QPoint(17, 15)])
cur.translate(260, 200)
p.setBrush(QColor("white"))
p.setPen(QPen(QColor("black"), 1.2))
p.drawPolygon(cur)
p.end()


ind = d.indicator
ind.show("Type text: Trial 2: 93, 80, 71…")
ind.frame.setGeometry(0, 0, W, H)
bw = max(560, ind.banner.sizeHint().width() + 40)
ind.banner.setGeometry(0, 0, bw, 58)
pump(0.2)
p = QPainter(desk)
ind.frame.render(p, QPoint(0, 0), renderFlags=QWidget.DrawChildren)
ind.banner.render(p, QPoint((W - bw) // 2, 16), renderFlags=QWidget.DrawChildren)
p.end()
save(desk, "06-computer-control-indicator.png")
ind.hide()

d.shutdown()
print("done")
