"""Linux desktop integration: autostart (.desktop), global hotkeys on X11 (python-xlib XGrabKey) and, on Wayland/GNOME,
custom keyboard shortcuts through gsettings (Wayland forbids apps to grab keys themselves; the shell runs
`grok-desktop --quick-ask` / `--stop` for us and the single-instance socket delivers it)."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import threading
import time

from . import APP_NAME

AUTOSTART_NAME = "grok-desktop.desktop"


def self_command() -> list[str]:
    """argv that starts this app (installed launcher, or `python -m grok_desktop` when run from source)."""
    exe = os.environ.get("GD_LAUNCHER") or shutil.which("grok-desktop")
    if exe:
        return [exe]
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return ["env", f"PYTHONPATH={src}", sys.executable, "-m", "grok_desktop"]


def _config_home() -> str:
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def autostart_path() -> str:
    return os.path.join(_config_home(), "autostart", AUTOSTART_NAME)


def autostart_enabled() -> bool:
    return os.path.isfile(autostart_path())


def set_autostart(on: bool) -> None:
    p = autostart_path()
    if not on:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(p), exist_ok=True)
    cmd = " ".join(shlex.quote(a) for a in self_command() + ["--minimized"])
    with open(p, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\nType=Application\nName=" + APP_NAME + "\nComment=Chat with Grok and let it use your computer\n"
                f"Exec={cmd}\nIcon=grok-desktop\nTerminal=false\nX-GNOME-Autostart-enabled=true\n"
                "X-GNOME-Autostart-Delay=5\n")


# ------------------------------------------------------------------ hotkeys ----
def _split(seq: str) -> tuple[list[str], str]:
    mods, key = [], ""
    for part in [p.strip().lower() for p in seq.split("+") if p.strip()]:
        if part in ("ctrl", "control", "alt", "shift", "win", "meta", "super"):
            mods.append(part)
        else:
            key = part
    if not key:
        raise ValueError(f"no key in '{seq}'")
    return mods, key


_KS = {"esc": "Escape", "escape": "Escape", "space": "space", "enter": "Return", "return": "Return", "tab": "Tab",
       "pause": "Pause", "backspace": "BackSpace", "delete": "Delete", "insert": "Insert", "home": "Home", "end": "End"}


def keysym_name(key: str) -> str:
    return _KS.get(key) or (key.upper() if key.startswith("f") and key[1:].isdigit() else key)


class XGrabber(threading.Thread):
    """XGrabKey on the root window (X11 only). callback(name) runs on this thread: pass something thread-safe."""

    def __init__(self, wanted: dict[str, str], callback, failed):
        super().__init__(daemon=True)
        self.wanted, self.callback, self.failed = wanted, callback, failed
        self._stop_evt = threading.Event()

    def run(self) -> None:
        try:
            from Xlib import X, XK, display
            from Xlib.error import CatchError
        except ImportError:
            for n, s in self.wanted.items():
                self.failed(n, s)
            return
        try:
            d = display.Display()
        except Exception:  # noqa: BLE001
            for n, s in self.wanted.items():
                self.failed(n, s)
            return
        root = d.screen().root
        masks = {"ctrl": X.ControlMask, "control": X.ControlMask, "alt": X.Mod1Mask, "shift": X.ShiftMask,
                 "win": X.Mod4Mask, "meta": X.Mod4Mask, "super": X.Mod4Mask}
        table: dict[tuple[int, int], str] = {}
        extra = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)   # caps lock / num lock don't matter
        for name, seq in self.wanted.items():
            try:
                mods, key = _split(seq)
                kc = d.keysym_to_keycode(XK.string_to_keysym(keysym_name(key)))
                if not kc:
                    raise ValueError(key)
                m = 0
                for x in mods:
                    m |= masks[x]
            except (ValueError, KeyError):
                self.failed(name, seq)
                continue
            ok = True
            for e in extra:
                err = CatchError()
                root.grab_key(kc, m | e, True, X.GrabModeAsync, X.GrabModeAsync, onerror=err)
                d.sync()
                if err.get_error():
                    ok = False
            if ok:
                table[(kc, m)] = name
            else:
                self.failed(name, seq)
        clean = ~(X.LockMask | X.Mod2Mask)
        while not self._stop_evt.is_set():
            try:
                while d.pending_events():
                    ev = d.next_event()
                    if ev.type == X.KeyPress:
                        name = table.get((ev.detail, ev.state & clean & 0xFF))
                        if name:
                            self.callback(name)
            except Exception:  # noqa: BLE001
                break
            time.sleep(0.05)
        try:
            for (kc, m) in table:
                for e in extra:
                    root.ungrab_key(kc, m | e)
            d.sync()
            d.close()
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        self._stop_evt.set()
        self.join(1.0)


# --------------------------------------------------- GNOME custom shortcuts ----
_MK = "org.gnome.settings-daemon.plugins.media-keys"
_BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"


def _gs(*args) -> str | None:
    try:
        p = subprocess.run(["gsettings", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def gnome_binding(seq: str) -> str:
    mods, key = _split(seq)
    names = {"ctrl": "<Control>", "control": "<Control>", "alt": "<Alt>", "shift": "<Shift>", "win": "<Super>",
             "meta": "<Super>", "super": "<Super>"}
    k = {"esc": "Escape", "escape": "Escape"}.get(key, key)
    return "".join(names[m] for m in mods) + k


def register_gnome_shortcuts(items: dict[str, tuple[str, str]]) -> bool:
    """items: {id: (binding sequence like 'Ctrl+Alt+G', command string)}. Idempotent. False if gsettings is missing."""
    cur = _gs("get", _MK, "custom-keybindings")
    if cur is None:
        return False
    import ast
    try:
        paths = ast.literal_eval(cur.replace("@as ", "")) or []
    except (ValueError, SyntaxError):
        paths = []
    for ident, (seq, cmd) in items.items():
        path = f"{_BASE}grok-desktop-{ident}/"
        schema = f"{_MK}.custom-keybinding:{path}"
        if not seq:
            if path in paths:
                paths.remove(path)
            continue
        _gs("set", schema, "name", f"{APP_NAME}: {ident}")
        _gs("set", schema, "command", cmd)
        _gs("set", schema, "binding", gnome_binding(seq))
        if path not in paths:
            paths.append(path)
    return _gs("set", _MK, "custom-keybindings", str(paths)) is not None


def is_gnome() -> bool:
    return "gnome" in (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower() or "zorin" in (
        os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
