"""Where things live, settings, and how to find/run `grok` (adapted from grok-app-studio/env.py).

Windows is the real target; the same code runs on Linux so it can be tested on the build box.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

from . import APP_ID

IS_WINDOWS = sys.platform == "win32"
HOME = os.path.expanduser("~")

# xAI's official installer (https://x.ai/cli): per-user, no admin, puts grok in %USERPROFILE%\.grok\bin.
WIN_INSTALL = 'powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://x.ai/cli/install.ps1 | iex"'
LINUX_INSTALL = "curl -fsSL https://x.ai/cli/install.sh | bash"
SHELL_NAME = "PowerShell" if IS_WINDOWS else "the terminal"
PC_NAME = "PC" if IS_WINDOWS else "computer"
INSTALL_COMMAND = WIN_INSTALL if IS_WINDOWS else LINUX_INSTALL

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_URL = re.compile(r"https://[^\s\"'<>]+")
_CODE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")


def data_dir() -> str:
    """%APPDATA%\\GrokDesktop (Linux: ~/.local/share/GrokDesktop). GD_HOME overrides (tests)."""
    if os.environ.get("GD_HOME"):
        d = os.environ["GD_HOME"]
    elif IS_WINDOWS:
        d = os.path.join(os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming"), APP_ID)
    else:
        d = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share"), APP_ID)
    os.makedirs(d, exist_ok=True)
    return d


def sub(name: str) -> str:
    p = os.path.join(data_dir(), name)
    os.makedirs(p, exist_ok=True)
    return p


def db_path() -> str:
    return os.path.join(data_dir(), "history.sqlite3")


def attachments_dir() -> str:
    return sub("attachments")


def workspace_dir() -> str:
    """grok's working folder (neutral; the MCP tools work on the whole PC)."""
    return sub("workspace")


def screenshots_dir() -> str:
    return sub("screenshots")


def no_window() -> dict:
    """subprocess kwargs so console programs don't flash a black window on Windows."""
    return {"creationflags": 0x08000000} if IS_WINDOWS else {}  # CREATE_NO_WINDOW


# ------------------------------------------------------------------ settings --
DEFAULT_SETTINGS = {
    "permission_mode": "ask",      # ask | safe | all
    "allow_all_warned": False,     # the one-time "Allow everything" warning was accepted
    "model": "",                   # "" = grok's default
    "tray": True,                  # show a tray icon
    "close_to_tray": False,        # closing the window keeps the app in the tray
    "quick_ask_hotkey": "Ctrl+Alt+G",
    "kill_hotkey": "Ctrl+Alt+Esc",
    "grok_path": "",
    "computer_control": True,      # give grok the grok-desktop MCP tools
    "voice_enabled": True,         # show the voice bar (mic, hands-free, mute, orb)
    "voice_speak": "voice",        # speak replies: voice (only after voice input) | always | off
    "voice_voice": "lessac-medium",
    "voice_speed": 1.0,
    "voice_ptt_key": "Ctrl+Space", # hold to talk (inside the chat window)
}


class Settings(dict):
    def __init__(self, path: str | None = None):
        super().__init__(DEFAULT_SETTINGS)
        self.path = path or os.path.join(data_dir(), "settings.json")
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(self), f, indent=1)
        os.replace(tmp, self.path)

    def set(self, key: str, value) -> None:
        self[key] = value
        self.save()


# ---------------------------------------------------------------------- grok --
def find_grok(configured: str = "") -> str | None:
    override = os.environ.get("GD_GROK_BIN")  # the tests' fake grok
    if override:
        return override
    names = ["grok.exe", "grok.cmd", "grok"] if IS_WINDOWS else ["grok"]
    cands = [configured] if configured else []
    cands += [shutil.which(n) or "" for n in names]
    cands += [os.path.join(HOME, ".grok", "bin", n) for n in names]
    if not IS_WINDOWS:
        cands += [os.path.join(HOME, ".local/bin/grok"), "/usr/bin/grok"]
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return None


def grok_version(path: str) -> str | None:
    try:
        p = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20, **no_window())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (p.stdout.strip() or None) if p.returncode == 0 else None


def parse_login_output(text: str) -> dict:
    """Pull the sign-in URL and device code out of `grok login` output (from gridos-companion)."""
    clean = _ANSI.sub("", text)
    url = next(iter(_URL.findall(clean)), "")
    codes = _CODE.findall(clean)
    return {"url": url.rstrip(".,)"), "code": codes[-1] if codes else ""}


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def mcp_server_command() -> tuple[str, list[str], dict]:
    """How grok should start our MCP tool server: (command, args, extra env)."""
    if getattr(sys, "frozen", False):  # PyInstaller build: the same exe in MCP mode
        return sys.executable, ["--mcp-server"], {}
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pp = os.pathsep.join([src] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else []))
    return sys.executable, ["-m", "grok_desktop", "--mcp-server"], {"PYTHONPATH": pp}
