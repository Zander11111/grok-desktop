import os
import sys
import time

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_grok.py")


def app():
    return QApplication.instance() or QApplication(sys.argv[:1])


def wait_until(pred, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    QCoreApplication.processEvents()
    return pred()


def pump(seconds):
    wait_until(lambda: False, seconds)


def fake_env(tmp_path, logged_in=True, speed="50"):
    home = tmp_path / "fakegrok"
    home.mkdir(exist_ok=True)
    if logged_in:
        (home / "logged_in").write_text("x")
    os.environ["MOCK_GROK_HOME"] = str(home)
    os.environ["MOCK_GROK_SPEED"] = speed
    os.environ["MOCK_LOGIN_DELAY"] = "0.3"
    os.environ["GD_GROK_BIN"] = FAKE
    os.environ["GD_HOME"] = str(tmp_path / "appdata")
    os.environ["GD_FAKE_DESKTOP"] = str(tmp_path / "desktop")
    for k in ("MOCK_RESUME_FAIL", "MOCK_IMAGES", "MOCK_NO_SET_MODEL"):
        os.environ.pop(k, None)
    return home


def received(home, method=None):
    import json
    out = []
    p = home / "acp-received.jsonl"
    if p.exists():
        for line in p.read_text().splitlines():
            m = json.loads(line)
            if method is None or m.get("method") == method:
                out.append(m)
    return out


def actions(tmp_path):
    import json
    p = tmp_path / "desktop" / "actions.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
