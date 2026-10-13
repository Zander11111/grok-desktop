"""Platform layer for the computer-control tools.

WinDesktop: Windows via ctypes only (no pyautogui/Pillow): SendInput for mouse/keyboard, GDI StretchBlt/PrintWindow
for screenshots, EnumWindows/SetForegroundWindow for windows, SHFileOperationW for the Recycle Bin.
FakeDesktop: used on Linux/in tests (GD_FAKE_DESKTOP=<dir>): records every action to <dir>/actions.jsonl and
returns synthetic screenshots, so the tests never move a real mouse.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

from . import png

IS_WINDOWS = sys.platform == "win32"


class Desktop:
    name = "base"

    def screens(self) -> list[dict]: ...
    def capture(self, left, top, width, height, out_w, out_h) -> bytes: ...
    def capture_window(self, handle, out_max_w) -> tuple[bytes, dict]: ...
    def windows(self) -> list[dict]: ...
    def focus(self, handle) -> bool: ...
    def cursor(self) -> tuple[int, int]: ...
    def move(self, x, y) -> None: ...
    def button(self, btn: str, down: bool) -> None: ...
    def scroll(self, notches: int, horizontal: bool = False) -> None: ...
    def type_char_units(self, text: str) -> None: ...
    def keys(self, names: list[str], down: bool) -> None: ...
    def release_all(self) -> None: ...
    def open(self, target: str, args: str = "") -> None: ...
    def recycle(self, path: str) -> None: ...
    def start_menu_apps(self) -> list[dict]: ...


# ===================================================================== fake ====
class FakeDesktop(Desktop):
    name = "fake"

    def __init__(self, folder: str):
        self.dir = folder
        os.makedirs(folder, exist_ok=True)
        self.pos = (100, 100)
        self.log_path = os.path.join(folder, "actions.jsonl")

    def _log(self, **kw):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), **kw}) + "\n")

    def screens(self):
        return [{"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080, "primary": True, "scale": 1.0},
                {"index": 1, "left": 1920, "top": 0, "width": 1280, "height": 1024, "primary": False, "scale": 1.0}]

    def _img(self, w, h):
        row = bytes(v for x in range(w) for v in (124 * x // max(w, 1), 92, 255 - 61 * x // max(w, 1)))
        return png.encode_rgb(w, h, row * h)

    def capture(self, left, top, width, height, out_w, out_h):
        self._log(op="capture", rect=[left, top, width, height], out=[out_w, out_h])
        return self._img(out_w, out_h)

    def capture_window(self, handle, out_max_w):
        w = next((x for x in self.windows() if str(x["handle"]) == str(handle)), None)
        if not w:
            raise ValueError("no such window")
        ow = min(w["width"], out_max_w)
        oh = max(1, w["height"] * ow // w["width"])
        self._log(op="capture_window", handle=handle)
        return self._img(ow, oh), {"left": w["left"], "top": w["top"], "width": w["width"], "height": w["height"]}

    def windows(self):
        return [{"handle": 1001, "title": "Untitled - Notepad", "process": "notepad.exe", "left": 200, "top": 150,
                 "width": 800, "height": 600, "minimized": False, "focused": True},
                {"handle": 1002, "title": "Steam", "process": "steam.exe", "left": 0, "top": 0, "width": 1280,
                 "height": 800, "minimized": True, "focused": False}]

    def focus(self, handle):
        self._log(op="focus", handle=handle)
        return True

    def cursor(self):
        return self.pos

    def move(self, x, y):
        self.pos = (int(x), int(y))
        self._log(op="move", x=int(x), y=int(y))

    def button(self, btn, down):
        self._log(op="button", button=btn, down=down)

    def scroll(self, notches, horizontal=False):
        self._log(op="scroll", notches=notches, horizontal=horizontal)

    def type_char_units(self, text):
        self._log(op="type", text=text)

    def keys(self, names, down):
        self._log(op="keys", keys=names, down=down)

    def release_all(self):
        self._log(op="release_all")

    def open(self, target, args=""):
        self._log(op="open", target=target, args=args)

    def recycle(self, path):
        bin_dir = os.path.join(self.dir, "RecycleBin")
        os.makedirs(bin_dir, exist_ok=True)
        shutil.move(path, os.path.join(bin_dir, f"{time.time_ns()}-{os.path.basename(path)}"))
        self._log(op="recycle", path=path)

    def start_menu_apps(self):
        return [{"name": "Notepad", "path": "notepad.exe"}, {"name": "Steam", "path": r"C:\fake\Steam.lnk"},
                {"name": "Spotify", "path": r"C:\fake\Spotify.lnk"}]


# ================================================================== windows ====
if IS_WINDOWS:  # pragma: no cover - exercised on Windows / under wine
    import ctypes
    from ctypes import wintypes as W

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32")
    kernel32 = ctypes.WinDLL("kernel32")
    shell32 = ctypes.WinDLL("shell32")
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
    except OSError:
        dwmapi = None
    try:
        shcore = ctypes.WinDLL("shcore")
    except OSError:
        shcore = None

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD), ("dwFlags", W.DWORD),
                    ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD), ("time", W.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", W.DWORD), ("wParamL", W.WORD), ("wParamH", W.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", W.DWORD), ("u", _INPUTUNION)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD),
                    ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
                    ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD),
                    ("biClrImportant", W.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", W.DWORD * 3)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD),
                    ("szDevice", W.WCHAR * 32)]

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", W.HWND), ("wFunc", W.UINT), ("pFrom", W.LPCWSTR), ("pTo", W.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", W.BOOL), ("hNameMappings", W.LPVOID),
                    ("lpszProgressTitle", W.LPCWSTR)]

    user32.SendInput.argtypes = [W.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.GetDC.restype = W.HDC
    user32.GetDC.argtypes = [W.HWND]
    user32.ReleaseDC.argtypes = [W.HWND, W.HDC]
    gdi32.CreateCompatibleDC.restype = W.HDC
    gdi32.CreateCompatibleDC.argtypes = [W.HDC]
    gdi32.CreateCompatibleBitmap.restype = W.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = W.HGDIOBJ
    gdi32.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [W.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [W.HDC]
    gdi32.StretchBlt.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HDC, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, W.DWORD]
    gdi32.SetStretchBltMode.argtypes = [W.HDC, ctypes.c_int]
    gdi32.SetBrushOrgEx.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    gdi32.GetDIBits.argtypes = [W.HDC, W.HBITMAP, W.UINT, W.UINT, ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), W.UINT]
    user32.PrintWindow.argtypes = [W.HWND, W.HDC, W.UINT]
    user32.GetWindowTextLengthW.argtypes = [W.HWND]
    user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    user32.GetWindowThreadProcessId.restype = W.DWORD
    user32.GetForegroundWindow.restype = W.HWND
    user32.GetWindow.restype = W.HWND
    user32.GetWindow.argtypes = [W.HWND, W.UINT]
    user32.GetWindowLongW.argtypes = [W.HWND, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [W.HWND]
    user32.IsIconic.argtypes = [W.HWND]
    user32.IsWindow.argtypes = [W.HWND]
    user32.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [W.HWND]
    user32.BringWindowToTop.argtypes = [W.HWND]
    user32.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
    user32.AttachThreadInput.argtypes = [W.DWORD, W.DWORD, W.BOOL]
    user32.VkKeyScanW.argtypes = [W.WCHAR]
    user32.VkKeyScanW.restype = ctypes.c_short
    user32.MonitorFromPoint.restype = W.HMONITOR
    user32.MonitorFromPoint.argtypes = [W.POINT, W.DWORD]
    kernel32.OpenProcess.restype = W.HANDLE
    kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    kernel32.CloseHandle.argtypes = [W.HANDLE]
    shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]

    WNDENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    MONITORENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, ctypes.POINTER(W.RECT), W.LPARAM)

    INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
    MOUSE_FLAGS = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}
    VK = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "windows": 0x5B, "cmd": 0x5B,
          "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "delete": 0x2E,
          "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22,
          "pgdn": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "space": 0x20, "printscreen": 0x2C,
          "capslock": 0x14, "menu": 0x5D, "apps": 0x5D, "volumeup": 0xAF, "volumedown": 0xAE, "volumemute": 0xAD,
          "playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1, "numlock": 0x90, "pause": 0x13}
    VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})
    EXTENDED = {0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x5B, 0x5D, 0x2C, 0x90}

    def _set_dpi_aware():
        try:
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # per-monitor v2
                return
        except (AttributeError, OSError):
            pass
        try:
            shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass

    class WinDesktop(Desktop):
        name = "windows"

        def __init__(self):
            _set_dpi_aware()

        # ---------------------------------------------------------- screen --
        def screens(self):
            out = []

            def cb(hmon, _hdc, _rc, _lp):
                mi = MONITORINFOEXW()
                mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
                user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
                r = mi.rcMonitor
                scale = 1.0
                if shcore is not None:
                    dx, dy = W.UINT(), W.UINT()
                    try:
                        if shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                            scale = dx.value / 96.0
                    except OSError:
                        pass
                out.append({"left": r.left, "top": r.top, "width": r.right - r.left, "height": r.bottom - r.top,
                            "primary": bool(mi.dwFlags & 1), "scale": scale, "device": mi.szDevice})
                return True

            user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
            out.sort(key=lambda m: (not m["primary"], m["left"], m["top"]))
            for i, m in enumerate(out):
                m["index"] = i
            if not out:
                out = [{"index": 0, "left": 0, "top": 0, "width": user32.GetSystemMetrics(0),
                        "height": user32.GetSystemMetrics(1), "primary": True, "scale": 1.0}]
            return out

        def _grab(self, src_dc, sx, sy, sw, sh, ow, oh) -> bytes:
            mem = gdi32.CreateCompatibleDC(src_dc)
            bmp = gdi32.CreateCompatibleBitmap(src_dc, ow, oh)
            old = gdi32.SelectObject(mem, bmp)
            try:
                gdi32.SetStretchBltMode(mem, 4)  # HALFTONE: good-looking downscale
                gdi32.SetBrushOrgEx(mem, 0, 0, None)
                if not gdi32.StretchBlt(mem, 0, 0, ow, oh, src_dc, sx, sy, sw, sh, 0x00CC0020 | 0x40000000):
                    raise OSError("StretchBlt failed")
                bi = BITMAPINFO()
                bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bi.bmiHeader.biWidth, bi.bmiHeader.biHeight = ow, -oh  # top-down
                bi.bmiHeader.biPlanes, bi.bmiHeader.biBitCount, bi.bmiHeader.biCompression = 1, 32, 0
                buf = ctypes.create_string_buffer(ow * oh * 4)
                gdi32.GetDIBits(mem, bmp, 0, oh, buf, ctypes.byref(bi), 0)
                return png.encode_bgra(ow, oh, buf.raw)
            finally:
                gdi32.SelectObject(mem, old)
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(mem)

        def capture(self, left, top, width, height, out_w, out_h):
            sdc = user32.GetDC(None)
            try:
                return self._grab(sdc, left, top, width, height, out_w, out_h)
            finally:
                user32.ReleaseDC(None, sdc)

        def _rect(self, h) -> dict:
            r = W.RECT()
            ok = False
            if dwmapi is not None:  # without the invisible resize borders
                ok = dwmapi.DwmGetWindowAttribute(W.HWND(h), 9, ctypes.byref(r), ctypes.sizeof(r)) == 0
            if not ok:
                user32.GetWindowRect(h, ctypes.byref(r))
            return {"left": r.left, "top": r.top, "width": r.right - r.left, "height": r.bottom - r.top}

        def capture_window(self, handle, out_max_w):
            h = int(handle)
            if not user32.IsWindow(h):
                raise ValueError("no such window")
            if user32.IsIconic(h):
                user32.ShowWindow(h, 9)
                time.sleep(0.3)
            rc = self._rect(h)
            w, hgt = max(1, rc["width"]), max(1, rc["height"])
            ow = min(w, out_max_w)
            oh = max(1, hgt * ow // w)
            # PrintWindow (PW_RENDERFULLCONTENT) works even when the window is covered.
            wdc = user32.GetDC(None)
            mem = gdi32.CreateCompatibleDC(wdc)
            r = W.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            fw, fh = max(1, r.right - r.left), max(1, r.bottom - r.top)
            bmp = gdi32.CreateCompatibleBitmap(wdc, fw, fh)
            old = gdi32.SelectObject(mem, bmp)
            try:
                if user32.PrintWindow(h, mem, 2):
                    ox, oy = rc["left"] - r.left, rc["top"] - r.top
                    return self._grab(mem, ox, oy, w, hgt, ow, oh), rc
            finally:
                gdi32.SelectObject(mem, old)
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(mem)
                user32.ReleaseDC(None, wdc)
            return self.capture(rc["left"], rc["top"], w, hgt, ow, oh), rc

        # --------------------------------------------------------- windows --
        def _proc_name(self, h) -> str:
            pid = W.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            hp = kernel32.OpenProcess(0x1000, False, pid.value)
            if not hp:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(1024)
                n = W.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(n)):
                    return os.path.basename(buf.value)
            finally:
                kernel32.CloseHandle(hp)
            return ""

        def windows(self):
            fg = user32.GetForegroundWindow()
            out = []

            def cb(h, _lp):
                if not user32.IsWindowVisible(h) or user32.GetWindow(h, 4):  # GW_OWNER
                    return True
                if user32.GetWindowLongW(h, -20) & 0x80:  # WS_EX_TOOLWINDOW
                    return True
                n = user32.GetWindowTextLengthW(h)
                if n <= 0:
                    return True
                if dwmapi is not None:
                    cloaked = ctypes.c_int(0)
                    dwmapi.DwmGetWindowAttribute(W.HWND(h), 14, ctypes.byref(cloaked), 4)
                    if cloaked.value:
                        return True
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(h, buf, n + 1)
                out.append({"handle": int(h), "title": buf.value, "process": self._proc_name(h), **self._rect(h),
                            "minimized": bool(user32.IsIconic(h)), "focused": h == fg})
                return True

            user32.EnumWindows(WNDENUMPROC(cb), 0)
            return out

        def focus(self, handle):
            h = int(handle)
            if not user32.IsWindow(h):
                return False
            if user32.IsIconic(h):
                user32.ShowWindow(h, 9)  # SW_RESTORE
            fg = user32.GetForegroundWindow()
            if fg == h:
                return True
            me = kernel32.GetCurrentThreadId()
            other = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            if other and other != me:
                user32.AttachThreadInput(me, other, True)
            try:
                # Windows only lets the process that sent the last input event take the foreground. A zero-length
                # mouse move counts and, unlike the usual Alt tap, doesn't open the target's menu bar (which used
                # to swallow the first typed characters).
                nudge = INPUT(type=INPUT_MOUSE)
                nudge.mi.dwFlags = 0x0001  # MOUSEEVENTF_MOVE, dx=dy=0
                self._send([nudge])
                user32.SetForegroundWindow(h)
                user32.BringWindowToTop(h)
            finally:
                if other and other != me:
                    user32.AttachThreadInput(me, other, False)
            time.sleep(0.15)
            return user32.GetForegroundWindow() == h

        # ----------------------------------------------------------- input --
        def _send(self, items: list) -> None:
            arr = (INPUT * len(items))(*items)
            user32.SendInput(len(items), arr, ctypes.sizeof(INPUT))

        def cursor(self):
            p = W.POINT()
            user32.GetCursorPos(ctypes.byref(p))
            return p.x, p.y

        def move(self, x, y):
            user32.SetCursorPos(int(x), int(y))

        def button(self, btn, down):
            i = INPUT(type=INPUT_MOUSE)
            i.mi.dwFlags = MOUSE_FLAGS[btn][0 if down else 1]
            self._send([i])

        def scroll(self, notches, horizontal=False):
            i = INPUT(type=INPUT_MOUSE)
            i.mi.dwFlags = 0x1000 if horizontal else 0x0800
            i.mi.mouseData = ctypes.c_ulong(int(notches) * 120 & 0xFFFFFFFF).value
            self._send([i])

        def _key(self, vk, up=False, scan=0, unicode=False):
            i = INPUT(type=INPUT_KEYBOARD)
            i.ki.wVk, i.ki.wScan = vk, scan
            fl = KEYEVENTF_KEYUP if up else 0
            if unicode:
                fl |= KEYEVENTF_UNICODE
            elif vk in EXTENDED:
                fl |= KEYEVENTF_EXTENDEDKEY
            i.ki.dwFlags = fl
            return i

        def type_char_units(self, text):
            items = []
            for ch in text:
                if ch == "\n":
                    items += [self._key(0x0D), self._key(0x0D, True)]
                elif ch == "\t":
                    items += [self._key(0x09), self._key(0x09, True)]
                elif ch == "\r":
                    continue
                else:
                    units = ch.encode("utf-16-le")
                    for k in range(0, len(units), 2):
                        u = int.from_bytes(units[k:k + 2], "little")
                        items += [self._key(0, scan=u, unicode=True), self._key(0, True, scan=u, unicode=True)]
            if items:
                self._send(items)

        def vk_of(self, name: str) -> tuple[int, bool]:
            n = name.strip().lower()
            if n in VK:
                return VK[n], False
            if len(name) == 1:
                r = user32.VkKeyScanW(name)
                if r != -1:
                    return r & 0xFF, bool(r & 0x100)
            raise ValueError(f"unknown key '{name}'")

        def keys(self, names, down):
            items = []
            for n in names if down else list(reversed(names)):
                vk, shift = self.vk_of(n)
                if shift and down:
                    items.append(self._key(0x10))
                items.append(self._key(vk, not down))
                if shift and not down:
                    items.append(self._key(0x10, True))
            self._send(items)

        def release_all(self):
            ups = [self._key(vk, True) for vk in (0x10, 0x11, 0x12, 0x5B, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5)
                   if user32.GetAsyncKeyState(vk) & 0x8000]
            for btn, vk in (("left", 0x01), ("right", 0x02), ("middle", 0x04)):
                if user32.GetAsyncKeyState(vk) & 0x8000:
                    i = INPUT(type=INPUT_MOUSE)
                    i.mi.dwFlags = MOUSE_FLAGS[btn][1]
                    ups.append(i)
            if ups:
                self._send(ups)

        # ------------------------------------------------------------ apps --
        def open(self, target, args=""):
            if args:
                os.startfile(target, "open", args)  # type: ignore[attr-defined]
            else:
                os.startfile(target)  # type: ignore[attr-defined]

        def recycle(self, path):
            op = SHFILEOPSTRUCTW()
            op.wFunc = 3  # FO_DELETE
            op.pFrom = os.path.abspath(path) + "\0"
            op.fFlags = 0x40 | 0x10 | 0x4 | 0x400  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
            rc = shell32.SHFileOperationW(ctypes.byref(op))
            if rc != 0 or op.fAnyOperationsAborted:
                raise OSError(f"Recycle Bin move failed (code {rc})")

        def start_menu_apps(self):
            roots = [os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
                     os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs"),
                     os.path.join(os.path.expanduser("~"), "Desktop"),
                     os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop")]
            seen, out = set(), []
            for root in roots:
                for dp, _dn, fns in os.walk(root):
                    for fn in fns:
                        stem, ext = os.path.splitext(fn)
                        if ext.lower() in (".lnk", ".url", ".appref-ms") and stem.lower() not in seen:
                            if "uninstall" in stem.lower():
                                continue
                            seen.add(stem.lower())
                            out.append({"name": stem, "path": os.path.join(dp, fn)})
            return out


def make_desktop() -> Desktop:
    fake = os.environ.get("GD_FAKE_DESKTOP")
    if fake:
        return FakeDesktop(fake)
    if IS_WINDOWS:
        return WinDesktop()
    if sys.platform.startswith("linux") and os.environ.get("GD_PLATFORM", "linux") == "linux":
        from .linux_desktop import LinuxDesktop
        return LinuxDesktop()
    return FakeDesktop(os.path.join(os.path.expanduser("~"), ".cache", "grok-desktop-fake"))
