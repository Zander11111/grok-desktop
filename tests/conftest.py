import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GD_PLATFORM"] = "windows"   # the original suite tests the Windows tool set (powershell tool) on the Linux box; test_linux.py overrides
os.environ["GD_NO_GLOBAL_HOTKEYS"] = "1"
# never touch the real home: history, settings and the fake grok's state go to a throwaway folder
os.environ["HOME"] = tempfile.mkdtemp(prefix="gd-test-home-")
for k in ("GD_GROK_BIN", "GD_HOME", "GD_FAKE_DESKTOP", "GD_CONTROL_PORT", "GD_CONTROL_TOKEN"):
    os.environ.pop(k, None)

import pytest


@pytest.fixture(autouse=True)
def _restore_env():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)
