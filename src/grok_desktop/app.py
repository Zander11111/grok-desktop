"""Start Grok Desktop: one instance per user, main window, tray icon, global hotkeys, control indicator."""
from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import APP_ID, APP_NAME, __version__, paths, theme
from .controller import Controller
from .hotkeys import GlobalHotkeys
from .overlay import ControlIndicator
from .ui import MainWindow, QuickAsk


class DesktopApp:
    def __init__(self, qapp: QApplication, ctl: Controller | None = None, show: bool = True):
        self.qapp = qapp
        self.ctl = ctl or Controller(paths.Settings())
        self.win = MainWindow(self.ctl)
        self.indicator = ControlIndicator(self.ctl.settings.get("kill_hotkey", "Ctrl+Alt+Esc"))
        self.indicator.stopClicked.connect(self.ctl.emergency_stop)
        self.ctl.controlActive.connect(self.indicator.set_active)
        self.ctl.killedChanged.connect(lambda k: k and self.indicator.hide())
        self.quick = QuickAsk()
        self.quick.asked.connect(self.quick_ask)
        self.hotkeys = GlobalHotkeys(self.win)
        self.hotkeys.triggered.connect(self._hotkey)
        self.hotkeys.failed.connect(lambda n, s: self.win.statusMessage(f"Couldn't register hotkey {s} (in use?)"))
        self._last_hotkey = (None, 0.0)
        self.tray: QSystemTrayIcon | None = None
        self.win.settingsClosed.connect(self.apply_settings)
        self.apply_settings()
        if show:
            self.win.show()
        QTimer.singleShot(0, self.ctl.start)

    def apply_settings(self) -> None:
        s = self.ctl.settings
        self.hotkeys.set({"kill": s.get("kill_hotkey", "Ctrl+Alt+Esc"), "quick": s.get("quick_ask_hotkey", "")})
        if s.get("tray") and QSystemTrayIcon.isSystemTrayAvailable():
            if self.tray is None:
                self.tray = QSystemTrayIcon(theme.app_icon())
                self.tray.setToolTip(APP_NAME)
                m = QMenu()
                for text, fn in ((f"Open {APP_NAME}", self.win.bring_to_front), ("Quick ask…", self.quick.popup),
                                 ("Emergency stop", self.ctl.emergency_stop), (None, None), ("Quit", self.quit)):
                    if text is None:
                        m.addSeparator()
                    else:
                        a = QAction(text, m)
                        a.triggered.connect(fn)
                        m.addAction(a)
                self.tray.setContextMenu(m)
                self.tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and self.win.bring_to_front())
                self._tray_menu = m
            self.tray.show()
        elif self.tray is not None:
            self.tray.hide()

    def _hotkey(self, name: str) -> None:
        last, ts = self._last_hotkey
        if last == name and time.time() - ts < 0.4:  # global + in-app shortcut fired together
            return
        self._last_hotkey = (name, time.time())
        if name == "kill":
            self.ctl.emergency_stop()
        elif name == "quick":
            self.quick.popup()

    def quick_ask(self, text: str) -> None:
        self.win.bring_to_front()
        if self.ctl.busy:
            self.ctl.stop()
        self.ctl.new_chat()
        QTimer.singleShot(50, lambda: self.ctl.send(text))

    def quit(self) -> None:
        self.win.force_quit = True
        self.shutdown()
        self.qapp.quit()

    def shutdown(self) -> None:
        self.hotkeys.stop()
        self.indicator.hide()
        if self.tray:
            self.tray.hide()
        self.ctl.shutdown()


def main() -> int:
    if "--version" in sys.argv:
        print(f"{APP_NAME} {__version__}")
        return 0
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    if sys.platform.startswith("linux"):   # fractional scaling under XWayland makes Qt repaint blurry/glitchy while scrolling
        os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "Round")
    if "--autostart" in sys.argv and sys.platform.startswith("linux"):   # --autostart on|off
        from . import linux_integration as li
        i = sys.argv.index("--autostart")
        li.set_autostart((sys.argv[i + 1] if len(sys.argv) > i + 1 else "on") != "off")
        return 0
    if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY") \
            and not os.environ.get("QT_QPA_PLATFORM") and not os.environ.get("GD_NATIVE_WAYLAND"):
        # Native Wayland windows can't position themselves or stay on top, which the control overlay and the quick-ask box
        # need; XWayland (xcb) can. Set GD_NATIVE_WAYLAND=1 to opt out.
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    qapp = QApplication(sys.argv[:1])
    qapp.setApplicationName(APP_ID)
    qapp.setApplicationDisplayName(APP_NAME)
    qapp.setDesktopFileName("grok-desktop")   # Linux: matches grok-desktop.desktop (icon/grouping in GNOME)
    qapp.setWindowIcon(theme.app_icon())
    qapp.setQuitOnLastWindowClosed(False)
    theme.apply(qapp)
    # one instance per user: a second launch just brings the first one up (or opens quick ask)
    name = f"{APP_ID}-{os.environ.get('USERNAME') or os.environ.get('USER') or 'user'}"
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(300):
        sock.write(b"stop" if "--stop" in sys.argv else b"quick" if "--quick-ask" in sys.argv else b"show")
        sock.waitForBytesWritten(500)
        return 0
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.listen(name)
    minimized = "--minimized" in sys.argv   # "start with Windows" (installer task): tray only, or a minimized window
    app = DesktopApp(qapp, show=not minimized)
    if minimized and app.tray is None:
        app.win.showMinimized()

    def incoming():
        c = server.nextPendingConnection()
        if c and c.waitForReadyRead(500):
            cmd = bytes(c.readAll())
            if cmd == b"stop":
                app.ctl.emergency_stop()
            else:
                (app.quick.popup if cmd == b"quick" else app.win.bring_to_front)()

    server.newConnection.connect(incoming)
    app.win.destroyed.connect(qapp.quit)
    orig_close = app.win.closeEvent

    def close_event(e):
        orig_close(e)
        if e.isAccepted():
            app.shutdown()
            qapp.quit()
    app.win.closeEvent = close_event
    if "--quick-ask" in sys.argv:
        QTimer.singleShot(300, app.quick.popup)
    return qapp.exec()
