"""System-wide hotkeys. Windows: RegisterHotKey on a background thread with its own message loop (works even
when another app has focus, e.g. while the AI is clicking around). Elsewhere: app-wide QShortcuts only."""
from __future__ import annotations

import os
import shlex
import sys
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut

from .paths import IS_WINDOWS

MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8, "meta": 0x8}
KEYS = {"esc": 0x1B, "escape": 0x1B, "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "pause": 0x13,
        "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23}
KEYS.update({f"f{i}": 0x6F + i for i in range(1, 25)})


def parse(seq: str) -> tuple[int, int]:
    """'Ctrl+Alt+Esc' -> (MOD_CONTROL|MOD_ALT, VK_ESCAPE). Raises ValueError."""
    mods, vk = 0, 0
    for part in [p.strip().lower() for p in seq.split("+") if p.strip()]:
        if part in MODS:
            mods |= MODS[part]
        elif part in KEYS:
            vk = KEYS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            raise ValueError(f"unknown key '{part}' in '{seq}'")
    if not vk:
        raise ValueError(f"no key in '{seq}'")
    return mods, vk


class GlobalHotkeys(QObject):
    triggered = Signal(str)   # name
    failed = Signal(str, str)  # name, sequence

    def __init__(self, parent_widget=None):
        super().__init__(parent_widget)
        self.parent_widget = parent_widget
        self.wanted: dict[str, str] = {}
        self.registered: dict[str, bool] = {}
        self._thread = None
        self._tid = 0
        self._shortcuts: list[QShortcut] = []

    def set(self, hotkeys: dict[str, str]) -> None:
        self.stop()
        self.wanted = {k: v for k, v in hotkeys.items() if v}
        if IS_WINDOWS:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        elif sys.platform.startswith("linux"):
            self._linux_start()
        if self.parent_widget is not None:  # also inside the app (and the only way on Linux)
            for name, seq in self.wanted.items():
                sc = QShortcut(QKeySequence(seq), self.parent_widget)
                sc.setContext(Qt.ApplicationShortcut)
                sc.activated.connect(lambda n=name: self.triggered.emit(n))
                self._shortcuts.append(sc)

    def _loop(self) -> None:  # pragma: no cover - Windows only
        import ctypes
        from ctypes import wintypes as W
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._tid = k32.GetCurrentThreadId()
        ids = {}
        for i, (name, seq) in enumerate(self.wanted.items(), start=1):
            try:
                mods, vk = parse(seq)
            except ValueError:
                self.failed.emit(name, seq)
                continue
            ok = bool(u32.RegisterHotKey(None, i, mods | 0x4000, vk))  # MOD_NOREPEAT
            self.registered[name] = ok
            if ok:
                ids[i] = name
            else:
                self.failed.emit(name, seq)
        msg = W.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312 and msg.wParam in ids:  # WM_HOTKEY
                self.triggered.emit(ids[msg.wParam])
        for i in ids:
            u32.UnregisterHotKey(None, i)

    # ---- Linux: XGrabKey on X11; on Wayland/GNOME the shell's custom shortcuts run `grok-desktop --stop` etc.
    def _linux_start(self) -> None:
        from . import linux_integration as li
        wayland = (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland") or (
            bool(os.environ.get("WAYLAND_DISPLAY")) and not os.environ.get("DISPLAY"))
        if os.environ.get("QT_QPA_PLATFORM") in ("offscreen", "minimal") or os.environ.get("GD_NO_GLOBAL_HOTKEYS"):
            return
        if not wayland and os.environ.get("DISPLAY"):
            self._xgrab = li.XGrabber(self.wanted, self.triggered.emit, self.failed.emit)   # Signal.emit is thread-safe
            self._xgrab.start()
        elif wayland and li.is_gnome():
            cmds = {"kill": "--stop", "quick": "--quick-ask"}
            base = " ".join(shlex.quote(a) for a in li.self_command())
            ok = li.register_gnome_shortcuts({n: (seq, f"{base} {cmds[n]}") for n, seq in self.wanted.items() if n in cmds})
            self.registered = {n: ok for n in self.wanted}
            if not ok:
                for n, seq in self.wanted.items():
                    self.failed.emit(n, seq)

    def stop(self) -> None:
        if getattr(self, "_xgrab", None) is not None:
            self._xgrab.stop()
            self._xgrab = None
        for sc in self._shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcuts = []
        if IS_WINDOWS and self._thread is not None and self._tid:  # pragma: no cover
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)  # WM_QUIT
            self._thread.join(1.0)
        self._thread, self._tid = None, 0
