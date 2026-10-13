"""Linux platform layer for the computer-control tools (the counterpart of WinDesktop in desktop.py).

Everything goes through small, widely packaged command-line tools, so nothing here needs a compiled extension:

  session   X11            Wayland (GNOME/KDE/wlroots)
  --------  -------------  -----------------------------------------------------------------------------------
  screen    maim/scrot/    grim (wlroots) / gnome-screenshot / spectacle     (+ Pillow or ImageMagick to scale)
            import/gnome-screenshot
  input     xdotool        ydotool (needs ydotoold + /dev/uinput access); without it xdotool only reaches
                           XWayland apps, so on a pure-Wayland session mouse/keyboard control reports a clear error
  windows   wmctrl/xdotool wmctrl lists XWayland windows only; kdotool on KDE
  open      xdg-open / gio launch        trash: gio trash (fallback trash-put, then a manual XDG trash write)

Wayland deliberately forbids apps to inject input or list other apps' windows; that is a platform rule, not a bug.
Set GD_FAKE_DESKTOP=<dir> to use the recording fake instead (tests).
"""
from __future__ import annotations

import configparser
import glob
import io
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time

from .desktop import Desktop

TIMEOUT = 20

XKEYS = {
    "ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt", "win": "super", "windows": "super",
    "cmd": "super", "super": "super", "meta": "super", "enter": "Return", "return": "Return", "tab": "Tab",
    "esc": "Escape", "escape": "Escape", "backspace": "BackSpace", "delete": "Delete", "del": "Delete",
    "insert": "Insert", "home": "Home", "end": "End", "pageup": "Prior", "pgup": "Prior", "pagedown": "Next",
    "pgdn": "Next", "left": "Left", "up": "Up", "right": "Right", "down": "Down", "space": "space",
    "printscreen": "Print", "capslock": "Caps_Lock", "menu": "Menu", "apps": "Menu", "numlock": "Num_Lock",
    "pause": "Pause", "volumeup": "XF86AudioRaiseVolume", "volumedown": "XF86AudioLowerVolume",
    "volumemute": "XF86AudioMute", "playpause": "XF86AudioPlay", "nexttrack": "XF86AudioNext",
    "prevtrack": "XF86AudioPrev",
}
XKEYS.update({f"f{i}": f"F{i}" for i in range(1, 25)})
PUNCT = {"+": "plus", "-": "minus", "=": "equal", ",": "comma", ".": "period", "/": "slash", "\\": "backslash",
         ";": "semicolon", "'": "apostrophe", "`": "grave", "[": "bracketleft", "]": "bracketright",
         "!": "exclam", "@": "at", "#": "numbersign", "$": "dollar", "%": "percent", "^": "asciicircum",
         "&": "ampersand", "*": "asterisk", "(": "parenleft", ")": "parenright", "_": "underscore", ":": "colon",
         '"': "quotedbl", "<": "less", ">": "greater", "?": "question", "{": "braceleft", "}": "braceright",
         "|": "bar", "~": "asciitilde", " ": "space"}

# linux input-event-codes for ydotool (US layout)
EV = {"esc": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11, "-": 12, "=": 13,
      "backspace": 14, "tab": 15, "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24,
      "p": 25, "[": 26, "]": 27, "enter": 28, "ctrl": 29, "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35,
      "j": 36, "k": 37, "l": 38, ";": 39, "'": 40, "`": 41, "shift": 42, "\\": 43, "z": 44, "x": 45, "c": 46, "v": 47,
      "b": 48, "n": 49, "m": 50, ",": 51, ".": 52, "/": 53, "alt": 56, "space": 57, "capslock": 58,
      "numlock": 69, "printscreen": 99, "home": 102, "up": 103, "pageup": 104, "left": 105, "right": 106, "end": 107,
      "down": 108, "pagedown": 109, "insert": 110, "delete": 111, "pause": 119, "super": 125, "menu": 127,
      "volumemute": 113, "volumedown": 114, "volumeup": 115, "playpause": 164, "nexttrack": 163, "prevtrack": 165}
EV.update({f"f{i}": 58 + i for i in range(1, 11)})
EV.update({"f11": 87, "f12": 88})
EV_ALIAS = {"control": "ctrl", "escape": "esc", "return": "enter", "del": "delete", "pgup": "pageup",
            "pgdn": "pagedown", "win": "super", "windows": "super", "cmd": "super", "meta": "super", "apps": "menu"}
BTN = {"left": 1, "middle": 2, "right": 3}


def session_type() -> str:
    t = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    if t in ("x11", "wayland"):
        return t
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    return "x11" if os.environ.get("DISPLAY") else "none"


def _run(argv, timeout=TIMEOUT, stdin=None, check=True) -> subprocess.CompletedProcess:
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout, input=stdin)
    except FileNotFoundError:
        raise OSError(f"'{argv[0]}' is not installed.") from None
    except subprocess.TimeoutExpired:
        raise OSError(f"'{argv[0]}' timed out.") from None
    if check and p.returncode != 0:
        raise OSError(f"{argv[0]} failed: {(p.stderr or p.stdout).decode('utf-8', 'replace').strip()[:300]}")
    return p


def _have(name: str) -> bool:
    return shutil.which(name) is not None


# ------------------------------------------------------------------ images ----
def process_png(data: bytes, crop: tuple[int, int, int, int] | None, out_w: int, out_h: int) -> bytes:
    """Crop (x, y, w, h in image pixels) and scale a PNG. Pillow if present, else ImageMagick, else unchanged."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        if crop:
            x, y, w, h = crop
            im = im.crop((max(0, x), max(0, y), min(im.width, x + w), min(im.height, y + h)))
        if im.size != (out_w, out_h):
            im = im.resize((out_w, out_h), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()
    except ImportError:
        pass
    exe = shutil.which("magick") or shutil.which("convert")
    if exe:
        argv = [exe, "png:-"]
        if crop:
            argv += ["-crop", f"{crop[2]}x{crop[3]}+{crop[0]}+{crop[1]}", "+repage"]
        argv += ["-resize", f"{out_w}x{out_h}!", "png:-"]
        return _run(argv, stdin=data).stdout
    if crop is None:
        return data
    raise OSError("Install python3-pil (Pillow) or imagemagick so screenshots can be cropped and scaled.")


def png_size(data: bytes) -> tuple[int, int]:
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


class LinuxDesktop(Desktop):
    name = "linux"

    def __init__(self):
        self.session = session_type()
        self.wayland = self.session == "wayland"
        self._pos = (0, 0)
        self._apps_cache: list[dict] | None = None

    # ------------------------------------------------------------ backends --
    def input_backend(self) -> str:
        if self.wayland:
            if _have("ydotool") and self._ydotool_ok():
                return "ydotool"
            if _have("wtype"):
                return "wtype"      # keyboard only
            return "none"
        return "xdotool" if _have("xdotool") else "none"

    def _ydotool_ok(self) -> bool:
        sock = os.environ.get("YDOTOOL_SOCKET") or f"/run/user/{os.getuid()}/.ydotool_socket"
        return os.path.exists(sock) or os.path.exists("/tmp/.ydotool_socket")

    def describe(self) -> dict:
        return {"session": self.session, "input_backend": self.input_backend(),
                "screenshot_backend": (self._shot_cmd() or ["none"])[0],
                "window_backend": "wmctrl" if _have("wmctrl") else ("xdotool" if _have("xdotool") else "none"),
                "note": ("Wayland session: window listing only sees XWayland apps; input needs ydotool."
                         if self.wayland else "")}

    def _need_input(self):
        b = self.input_backend()
        if b == "none" or (b == "wtype"):
            if self.wayland:
                raise OSError("This is a Wayland session, which doesn't allow apps to control the mouse. Install and "
                              "start ydotool (sudo apt install ydotool; sudo systemctl enable --now ydotoold, or run "
                              "ydotoold and add yourself to the 'input' group), or log in with 'Zorin/GNOME on Xorg' "
                              "at the login screen (gear icon) for full control.")
            raise OSError("xdotool is not installed (sudo apt install xdotool).")
        return b

    def _xdo(self, *args, check=True):
        return _run(["xdotool", *map(str, args)], check=check)

    def _yd(self, *args):
        env = dict(os.environ)
        if "YDOTOOL_SOCKET" not in env and not os.path.exists(f"/run/user/{os.getuid()}/.ydotool_socket") \
                and os.path.exists("/tmp/.ydotool_socket"):
            env["YDOTOOL_SOCKET"] = "/tmp/.ydotool_socket"
        try:
            p = subprocess.run(["ydotool", *map(str, args)], capture_output=True, timeout=TIMEOUT, env=env)
        except FileNotFoundError:
            raise OSError("ydotool is not installed.") from None
        if p.returncode != 0:
            raise OSError("ydotool failed: " + (p.stderr or p.stdout).decode("utf-8", "replace").strip()[:200])

    # ------------------------------------------------------------- screens --
    def screens(self):
        out = []
        if _have("xrandr"):
            p = _run(["xrandr", "--query"], check=False)
            for m in re.finditer(r"^(\S+) connected( primary)? (\d+)x(\d+)\+(-?\d+)\+(-?\d+)", p.stdout.decode("utf-8", "replace"),
                                 re.M):
                out.append({"index": len(out), "left": int(m[5]), "top": int(m[6]), "width": int(m[3]),
                            "height": int(m[4]), "primary": bool(m[2]), "scale": 1.0, "name": m[1]})
        if not out and _have("xdotool"):
            p = _run(["xdotool", "getdisplaygeometry"], check=False)
            try:
                w, h = map(int, p.stdout.split())
                out = [{"index": 0, "left": 0, "top": 0, "width": w, "height": h, "primary": True, "scale": 1.0}]
            except ValueError:
                pass
        if not out:
            out = [{"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080, "primary": True, "scale": 1.0}]
        if not any(s["primary"] for s in out):
            out[0]["primary"] = True
        # primary first (the tools default to monitor 0 = the main screen)
        out.sort(key=lambda s: (not s["primary"], s["left"], s["top"]))
        for i, s in enumerate(out):
            s["index"] = i
        return out

    def _shot_cmd(self, path: str = "{}") -> list[str] | None:
        x11 = [("maim", ["maim", "-u", path]), ("scrot", ["scrot", "-o", "-z", path]),
               ("import", ["import", "-window", "root", path]), ("gnome-screenshot", ["gnome-screenshot", "-f", path])]
        wl = [("grim", ["grim", path]), ("gnome-screenshot", ["gnome-screenshot", "-f", path]),
              ("spectacle", ["spectacle", "-b", "-n", "-f", "-o", path])]
        for name, argv in (wl + x11 if self.wayland else x11 + [wl[0], wl[2]]):
            if _have(name):
                return argv
        return None

    def _grab_full(self) -> bytes:
        argv = self._shot_cmd()
        if not argv:
            raise OSError("No screenshot tool found. Install one: sudo apt install gnome-screenshot (or maim / scrot; "
                          "grim on wlroots compositors).")
        fd, tmp = tempfile.mkstemp(suffix=".png", prefix="gd-shot-")
        os.close(fd)
        try:
            os.unlink(tmp)
            _run([a.replace("{}", tmp) for a in argv], timeout=30)
            for _ in range(20):  # some tools return before the file is complete
                if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                    break
                time.sleep(0.1)
            with open(tmp, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _virtual(self) -> tuple[int, int, int, int]:
        sc = self.screens()
        l, t = min(s["left"] for s in sc), min(s["top"] for s in sc)
        return l, t, max(s["left"] + s["width"] for s in sc) - l, max(s["top"] + s["height"] for s in sc) - t

    def _crop_from_full(self, full: bytes, left, top, width, height) -> tuple[bytes, tuple[int, int, int, int]]:
        vl, vt, vw, vh = self._virtual()
        iw, ih = png_size(full)
        fx, fy = iw / vw, ih / vh
        # a single-screen tool (gnome-screenshot on one monitor) may return just that screen: treat as origin
        return full, (round((left - vl) * fx), round((top - vt) * fy), round(width * fx), round(height * fy))

    def capture(self, left, top, width, height, out_w, out_h):
        full = self._grab_full()
        full, crop = self._crop_from_full(full, left, top, width, height)
        iw, ih = png_size(full)
        if crop == (0, 0, iw, ih) and (out_w, out_h) == (iw, ih):
            return full
        return process_png(full, crop, out_w, out_h)

    def capture_window(self, handle, out_max_w):
        w = next((x for x in self.windows() if str(x["handle"]) == str(handle)), None)
        if not w:
            raise ValueError("no such window")
        ow = min(w["width"], out_max_w)
        oh = max(1, w["height"] * ow // max(1, w["width"]))
        rc = {"left": w["left"], "top": w["top"], "width": w["width"], "height": w["height"]}
        if not self.wayland and _have("maim"):
            try:
                raw = _run(["maim", "-u", "-i", str(handle)]).stdout
                return process_png(raw, None, ow, oh), rc
            except OSError:
                pass
        full, crop = self._crop_from_full(self._grab_full(), w["left"], w["top"], w["width"], w["height"])
        return process_png(full, crop, ow, oh), rc

    # ------------------------------------------------------------- windows --
    def _active(self) -> int:
        try:
            if _have("xdotool"):
                return int(_run(["xdotool", "getactivewindow"], check=False).stdout.strip() or 0)
        except ValueError:
            pass
        return 0

    def _minimized(self, wid: int) -> bool:
        if not _have("xprop"):
            return False
        p = _run(["xprop", "-id", str(wid), "_NET_WM_STATE"], check=False)
        return b"_NET_WM_STATE_HIDDEN" in p.stdout

    def windows(self):
        out = []
        if _have("wmctrl"):
            active = self._active()
            p = _run(["wmctrl", "-lGpx"], check=False)
            for line in p.stdout.decode("utf-8", "replace").splitlines():
                parts = line.split(None, 9)
                if len(parts) < 10:
                    continue
                try:
                    wid = int(parts[0], 16)
                    desk, pid = int(parts[1]), int(parts[2])
                    x, y, w, h = (int(v) for v in parts[3:7])
                except ValueError:
                    continue
                title = parts[9].strip()
                if not title or desk == -1:    # panels/desktop
                    continue
                try:
                    proc = open(f"/proc/{pid}/comm").read().strip()
                except OSError:
                    proc = parts[7].split(".")[-1]
                out.append({"handle": wid, "title": title, "process": proc, "left": x, "top": y, "width": w,
                            "height": h, "minimized": self._minimized(wid) if len(out) < 40 else False,
                            "focused": wid == active})
        elif _have("xdotool"):
            active = self._active()
            for sid in _run(["xdotool", "search", "--onlyvisible", "--name", "."], check=False).stdout.split()[:60]:
                try:
                    wid = int(sid)
                    title = _run(["xdotool", "getwindowname", sid], check=False).stdout.decode("utf-8", "replace").strip()
                    g = dict(kv.split("=") for kv in _run(["xdotool", "getwindowgeometry", "--shell", sid],
                                                           check=False).stdout.decode().split())
                    if title and int(g.get("WIDTH", 0)) > 50:
                        out.append({"handle": wid, "title": title, "process": "", "left": int(g["X"]), "top": int(g["Y"]),
                                    "width": int(g["WIDTH"]), "height": int(g["HEIGHT"]), "minimized": False,
                                    "focused": wid == active})
                except (ValueError, KeyError):
                    continue
        elif _have("kdotool"):
            for wid in _run(["kdotool", "search", "--name", "."], check=False).stdout.decode().split():
                title = _run(["kdotool", "getwindowname", wid], check=False).stdout.decode("utf-8", "replace").strip()
                if title:
                    out.append({"handle": wid, "title": title, "process": "", "left": 0, "top": 0, "width": 0,
                                "height": 0, "minimized": False, "focused": False})
        else:
            raise OSError("Can't list windows: install wmctrl (sudo apt install wmctrl). On Wayland only XWayland "
                          "windows can be listed.")
        return out

    def focus(self, handle):
        if _have("wmctrl"):
            return _run(["wmctrl", "-i", "-a", hex(int(handle))], check=False).returncode == 0
        if _have("xdotool"):
            return self._xdo("windowactivate", handle, check=False).returncode == 0
        if _have("kdotool"):
            return _run(["kdotool", "windowactivate", str(handle)], check=False).returncode == 0
        raise OSError("Can't focus windows: install wmctrl.")

    # --------------------------------------------------------------- input --
    def cursor(self):
        if not self.wayland and _have("xdotool"):
            p = self._xdo("getmouselocation", "--shell", check=False)
            kv = dict(l.split("=") for l in p.stdout.decode().split() if "=" in l)
            try:
                self._pos = (int(kv["X"]), int(kv["Y"]))
            except (KeyError, ValueError):
                pass
        return self._pos

    def move(self, x, y):
        b = self._need_input()
        self._pos = (int(x), int(y))
        if b == "xdotool":
            self._xdo("mousemove", int(x), int(y))
        else:
            self._yd("mousemove", "--absolute", "-x", int(x), "-y", int(y))

    def button(self, btn, down):
        b = self._need_input()
        if b == "xdotool":
            self._xdo("mousedown" if down else "mouseup", BTN[btn])
        else:
            code = {"left": 0x40, "right": 0x41, "middle": 0x42}[btn] + (0 if down else 0x40)
            self._yd("click", hex(code))

    def scroll(self, notches, horizontal=False):
        b = self._need_input()
        n = int(notches)
        if b == "xdotool":
            btn = (7 if n > 0 else 6) if horizontal else (4 if n > 0 else 5)
            self._xdo("click", "--repeat", abs(n) or 1, btn)
        else:
            self._yd("mousemove", "-w", "-x", n if horizontal else 0, "-y", 0 if horizontal else n)

    def type_char_units(self, text):
        b = self._need_input()
        if b == "xdotool":
            self._xdo("type", "--clearmodifiers", "--delay", 4, "--", text)
        else:
            self._yd("type", "--", text)

    def _xname(self, n: str) -> str:
        k = n.strip().lower()
        if k in XKEYS:
            return XKEYS[k]
        if len(n) == 1:
            return PUNCT.get(n, n)
        raise ValueError(f"unknown key '{n}'")

    def _ev(self, n: str) -> int:
        k = n.strip().lower()
        k = EV_ALIAS.get(k, k)
        if k in EV:
            return EV[k]
        raise ValueError(f"unknown key '{n}' (ydotool backend)")

    def keys(self, names, down):
        b = self._need_input()
        seq = names if down else list(reversed(names))
        if b == "xdotool":
            args = []
            for n in seq:
                args += ["keydown" if down else "keyup", self._xname(n)]
            self._xdo(*args)
        else:
            self._yd("key", *[f"{self._ev(n)}:{1 if down else 0}" for n in seq])

    def release_all(self):
        try:
            b = self.input_backend()
            if b == "xdotool":
                args = []
                for k in ("shift", "ctrl", "alt", "super", "Shift_R", "Control_R", "Alt_R", "Super_R"):
                    args += ["keyup", k]
                for n in (1, 2, 3):
                    args += ["mouseup", n]
                self._xdo(*args, check=False)
            elif b == "ydotool":
                self._yd("key", *[f"{c}:0" for c in (42, 29, 56, 125, 54, 97, 100)])
                for code in (0x80, 0x81, 0x82):
                    self._yd("click", hex(code))
        except OSError:
            pass

    # ---------------------------------------------------------------- apps --
    def open(self, target, args=""):
        env = dict(os.environ)
        for k in ("PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH", "QT_PLUGIN_PATH"):
            env.pop(k, None)   # don't leak our bundled Qt/Python into the app we start

        def spawn(argv):
            try:
                subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True, env=env)
            except FileNotFoundError:
                raise OSError(f"'{argv[0]}' is not installed.") from None

        extra = shlex.split(args) if args else []
        if target.endswith(".desktop") and os.path.isfile(target):
            if _have("gio"):
                spawn(["gio", "launch", target, *extra])
                return
            ex = self._desktop_exec(target)
            if ex:
                spawn(shlex.split(ex) + extra)
                return
        if os.path.isfile(target) and os.access(target, os.X_OK) and not target.endswith((".txt", ".md")):
            spawn([target, *extra])
        elif os.path.exists(target) or re.match(r"^[a-z][a-z0-9+.-]*:", target):
            spawn(["xdg-open", target])
        else:
            spawn([target, *extra])

    @staticmethod
    def _desktop_exec(path: str) -> str:
        cp = configparser.RawConfigParser(strict=False, interpolation=None)
        cp.optionxform = str
        try:
            cp.read(path, encoding="utf-8")
            return re.sub(r"\s%[a-zA-Z]", "", cp.get("Desktop Entry", "Exec"))
        except (configparser.Error, OSError):
            return ""

    def recycle(self, path):
        if _have("gio"):
            p = _run(["gio", "trash", path], check=False)
            if p.returncode == 0:
                return
        if _have("trash-put"):
            _run(["trash-put", path])
            return
        # manual XDG trash in the home partition
        base = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "Trash")
        os.makedirs(os.path.join(base, "files"), exist_ok=True)
        os.makedirs(os.path.join(base, "info"), exist_ok=True)
        name = os.path.basename(path.rstrip("/")) or "item"
        dest, n = name, 1
        while os.path.lexists(os.path.join(base, "files", dest)):
            n += 1
            dest = f"{name}.{n}"
        with open(os.path.join(base, "info", dest + ".trashinfo"), "w", encoding="utf-8") as f:
            f.write("[Trash Info]\nPath=" + os.path.abspath(path) + "\nDeletionDate=" + time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
        shutil.move(path, os.path.join(base, "files", dest))

    def start_menu_apps(self):
        if self._apps_cache is not None:
            return self._apps_cache
        dirs = [os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "applications")]
        for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
            dirs.append(os.path.join(d, "applications"))
        dirs += ["/var/lib/flatpak/exports/share/applications", os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
                 "/var/lib/snapd/desktop/applications"]
        seen, out = set(), []
        for d in dirs:
            for path in sorted(glob.glob(os.path.join(d, "**", "*.desktop"), recursive=True)):
                cp = configparser.RawConfigParser(strict=False, interpolation=None)
                cp.optionxform = str
                try:
                    cp.read(path, encoding="utf-8")
                    e = cp["Desktop Entry"]
                except (configparser.Error, KeyError, OSError, UnicodeDecodeError):
                    continue
                if e.get("NoDisplay", "").lower() == "true" or e.get("Hidden", "").lower() == "true" \
                        or e.get("Type", "Application") != "Application":
                    continue
                nm = e.get("Name", "").strip()
                if nm and nm.lower() not in seen:
                    seen.add(nm.lower())
                    out.append({"name": nm, "path": path})
        self._apps_cache = out
        return out
