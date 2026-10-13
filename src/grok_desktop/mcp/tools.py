"""The grok-desktop tools. Each takes (ctx, **arguments) and returns a dict with "ok" and "message"
(plus data). A tool may add "_image": (base64, mime) to send a picture back to the model."""
from __future__ import annotations

import base64
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import time

from . import png
from .catalog import expand, plat as catalog_plat
from .desktop import Desktop
from .link import Aborted, BaseLink

IS_WINDOWS = sys.platform == "win32"


def _win_mode() -> bool:
    return catalog_plat() == "windows"


MAX_READ = 20000
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _ok(message: str, **data) -> dict:
    return {"ok": True, "message": message, **data}


def _fail(message: str, **data) -> dict:
    return {"ok": False, "message": message, **data}


class Ctx:
    def __init__(self, desktop: Desktop, link: BaseLink, shots_dir: str | None = None):
        self.desktop = desktop
        self.link = link
        self.shots_dir = shots_dir
        prim = next((s for s in desktop.screens() if s.get("primary")), None) or {"left": 0, "top": 0}
        self.map = {"left": prim["left"], "top": prim["top"], "scale": 1.0, "source": "primary screen (no screenshot yet)"}

    def to_screen(self, x, y) -> tuple[int, int]:
        m = self.map
        return int(round(m["left"] + float(x) / m["scale"])), int(round(m["top"] + float(y) / m["scale"]))

    def from_screen(self, x, y) -> tuple[int, int]:
        m = self.map
        return int(round((x - m["left"]) * m["scale"])), int(round((y - m["top"]) * m["scale"]))


# ===================================================================== files ====
_PROTECTED = re.compile(r"^([a-z]:)?[\\/]?$|^[a-z]:[\\/](windows|program files( \(x86\))?|programdata|users)[\\/]?$", re.I)
_PROTECTED_LINUX = re.compile(r"^/(bin|boot|dev|etc|home|lib|lib32|lib64|opt|proc|root|run|sbin|srv|sys|usr|var|mnt|media|snap)?/?$"
                              r"|^/(usr|var)/(bin|lib|local|share)/?$")


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fs_list(ctx, path, pattern="", show_hidden=False):
    p = expand(path)
    if not os.path.isdir(p):
        return _fail(f"{p} is not a folder.")
    items = []
    with os.scandir(p) as it:
        for e in it:
            if not show_hidden and e.name.startswith("."):
                continue
            if pattern and not fnmatch.fnmatch(e.name.lower(), pattern.lower()):
                continue
            try:
                st = e.stat()
                items.append({"name": e.name, "type": "folder" if e.is_dir() else "file",
                              "size": None if e.is_dir() else st.st_size,
                              "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))})
            except OSError:
                items.append({"name": e.name, "type": "?"})
    items.sort(key=lambda i: (i["type"] != "folder", i["name"].lower()))
    shown = items[:500]
    return _ok(f"{len(items)} item(s) in {p}" + (" (first 500 shown)" if len(items) > 500 else ""), path=p, items=shown)


def fs_read(ctx, path, offset=0, max_chars=MAX_READ):
    p = expand(path)
    if not os.path.isfile(p):
        return _fail(f"No file at {p}.")
    if os.path.splitext(p)[1].lower() in png.MIME:
        return view_image(ctx, p)
    max_chars = max(1, min(int(max_chars or MAX_READ), 200000))
    with open(p, "rb") as f:
        raw = f.read(8_000_000)
    if b"\x00" in raw[:4096]:
        return _fail(f"{p} looks like a binary file ({_human(os.path.getsize(p))}); I can only read text.")
    text = raw.decode("utf-8", "replace")
    chunk = text[int(offset or 0):int(offset or 0) + max_chars]
    more = len(text) > int(offset or 0) + max_chars
    return _ok(f"Read {len(chunk):,} characters from {p}" + (" (more available: use offset)" if more else ""),
               path=p, content=chunk, total_chars=len(text), truncated=more)


def fs_write(ctx, path, content, overwrite=False, append=False):
    p = expand(path)
    if os.path.isdir(p):
        return _fail(f"{p} is a folder.")
    exists = os.path.exists(p)
    if exists and not (overwrite or append):
        return _fail(f"{p} already exists. Pass overwrite=true to replace it (or append=true).")
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8", newline="") as f:
        f.write(content)
    verb = "Appended to" if append and exists else ("Replaced" if exists else "Created")
    return _ok(f"{verb} {p} ({len(content):,} characters).", path=p)


def fs_move(ctx, source, destination, overwrite=False):
    s, d = expand(source), expand(destination)
    if not os.path.exists(s):
        return _fail(f"Nothing at {s}.")
    if os.path.isdir(d):
        d = os.path.join(d, os.path.basename(s))
    if os.path.exists(d):
        if not overwrite:
            return _fail(f"{d} already exists. Pass overwrite=true to replace it.")
        if os.path.isdir(d):
            return _fail(f"{d} is an existing folder; I won't replace a folder.")
        os.remove(d)
    os.makedirs(os.path.dirname(d) or ".", exist_ok=True)
    shutil.move(s, d)
    return _ok(f"Moved {s} → {d}.", source=s, destination=d)


def fs_delete(ctx, path, permanent=False):
    p = expand(path)
    if not os.path.lexists(p):
        return _fail(f"Nothing at {p}.")
    home = os.path.normcase(os.path.abspath(os.path.expanduser("~")))
    if (_PROTECTED_LINUX.match(p) if not IS_WINDOWS and not _win_mode() else _PROTECTED.match(p)) \
            or os.path.normcase(p) == home:
        return _fail(f"Refusing to delete {p}: it's a drive, a system folder or your whole home folder.")
    if permanent:
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
        return _ok(f"Permanently deleted {p}.", path=p)
    ctx.desktop.recycle(p)
    return _ok(f"Moved {p} to the {'Recycle Bin' if _win_mode() else 'Trash'}.", path=p)


def fs_search(ctx, root, name="", contains="", limit=50, max_depth=6):
    r = expand(root)
    if not os.path.isdir(r):
        return _fail(f"{r} is not a folder.")
    limit = max(1, min(int(limit or 50), 500))
    max_depth = max(0, min(int(max_depth if max_depth is not None else 6), 20))
    pat = (name or "*").lower()
    needle = (contains or "").lower()
    found, scanned, t0 = [], 0, time.time()
    base_depth = r.rstrip("\\/").count(os.sep)
    for dp, dns, fns in os.walk(r):
        dns[:] = [d for d in dns if not d.startswith(".") and d.lower() not in ("node_modules", "$recycle.bin", "appdata")]
        if dp.count(os.sep) - base_depth >= max_depth:
            dns[:] = []
        for fn in fns:
            scanned += 1
            if not fnmatch.fnmatch(fn.lower(), pat):
                continue
            fp = os.path.join(dp, fn)
            if needle:
                try:
                    if os.path.getsize(fp) > 5_000_000:
                        continue
                    with open(fp, "rb") as f:
                        if needle not in f.read().decode("utf-8", "ignore").lower():
                            continue
                except OSError:
                    continue
            found.append(fp)
            if len(found) >= limit:
                break
        if len(found) >= limit or time.time() - t0 > 25:
            break
    return _ok(f"Found {len(found)} file(s) under {r}" + (" (limit reached)" if len(found) >= limit else ""),
               files=found, scanned=scanned)


def view_image(ctx, path, max_width=0):
    p = expand(path)
    ext = os.path.splitext(p)[1].lower()
    if not os.path.isfile(p):
        return _fail(f"No file at {p}.")
    if ext not in png.MIME:
        return _fail("That's not an image I can show (PNG, JPEG, GIF, BMP, WebP).")
    size = os.path.getsize(p)
    if size > MAX_IMAGE_BYTES:
        return _fail(f"The image is too big to send ({_human(size)}; max 8 MB).")
    data = open(p, "rb").read()
    wh = png.size_of(data)
    return _ok(f"Image {os.path.basename(p)}" + (f" ({wh[0]}×{wh[1]})" if wh else ""), path=p,
               _image=(base64.b64encode(data).decode(), png.MIME[ext]))


# ================================================================ PowerShell ====
def _ps_exe() -> list[str] | None:
    for n in ("pwsh", "powershell") if not IS_WINDOWS else ("powershell", "pwsh"):
        w = shutil.which(n)
        if w:
            return [w, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    return None


def powershell(ctx, command, cwd="", timeout_s=60):
    timeout_s = max(1, min(int(timeout_s or 60), 600))
    wd = expand(cwd) if cwd else os.path.expanduser("~")
    exe = _ps_exe()
    if exe:
        script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; " + command
        argv = exe + ["-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()]
    elif not IS_WINDOWS:  # test fallback on the Linux build box
        argv = ["sh", "-c", command]
    else:
        return _fail("PowerShell wasn't found.")
    try:
        p = subprocess.run(argv, cwd=wd, capture_output=True, timeout=timeout_s,
                           creationflags=0x08000000 if IS_WINDOWS else 0)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace")
        return _fail(f"Timed out after {timeout_s}s.", stdout=out[-MAX_READ:])
    out, err = p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    msg = f"Exit code {p.returncode}."
    return {"ok": p.returncode == 0, "message": msg, "exit_code": p.returncode,
            "stdout": out[-MAX_READ:], "stderr": err[-5000:], "truncated": len(out) > MAX_READ}


def shell(ctx, command, cwd="", timeout_s=60):
    """bash on Linux (also usable for tests). Output is capped like powershell()."""
    timeout_s = max(1, min(int(timeout_s or 60), 600))
    wd = expand(cwd) if cwd else os.path.expanduser("~")
    sh = shutil.which("bash") or shutil.which("sh")
    if not sh:
        return _fail("No shell found.")
    try:
        proc = subprocess.Popen([sh, "-c", command], cwd=wd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
    except OSError as e:
        return _fail(f"Couldn't run it: {e}")
    try:
        so, se = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)   # the whole group, or a leftover child keeps the pipes open
        except OSError:
            pass
        so, _ = proc.communicate()
        return _fail(f"Timed out after {timeout_s}s.", stdout=(so or b"").decode("utf-8", "replace")[-MAX_READ:])
    p = subprocess.CompletedProcess([], proc.returncode, so, se)
    out, err = p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    return {"ok": p.returncode == 0, "message": f"Exit code {p.returncode}.", "exit_code": p.returncode,
            "stdout": out[-MAX_READ:], "stderr": err[-5000:], "truncated": len(out) > MAX_READ}


# ==================================================================== screen ====
def _save_shot(ctx, data: bytes) -> str:
    if not ctx.shots_dir:
        return ""
    os.makedirs(ctx.shots_dir, exist_ok=True)
    path = os.path.join(ctx.shots_dir, time.strftime("shot-%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000:03d}.png")
    with open(path, "wb") as f:
        f.write(data)
    shots = sorted(fn for fn in os.listdir(ctx.shots_dir) if fn.startswith("shot-"))
    for old in shots[:-30]:  # keep the last 30
        try:
            os.remove(os.path.join(ctx.shots_dir, old))
        except OSError:
            pass
    return path


def _find_window(ctx, window: str) -> dict | None:
    wins = ctx.desktop.windows()
    w = str(window or "").strip()
    if w.isdigit():
        return next((x for x in wins if str(x["handle"]) == w), None)
    lw = w.lower()
    matches = [x for x in wins if lw in x["title"].lower() or lw == (x.get("process") or "").lower().removesuffix(".exe")]
    matches.sort(key=lambda x: (x["title"].lower() != lw, x["minimized"], not x["focused"]))
    return matches[0] if matches else None


def screenshot(ctx, target="screen", monitor=0, window="", max_width=1280):
    max_width = max(320, min(int(max_width or 1280), 2560))
    if target == "window" or window:
        win = _find_window(ctx, window)
        if not win:
            return _fail(f"No open window matches '{window}'. Use list_windows.")
        data, rc = ctx.desktop.capture_window(win["handle"], max_width)
        wh = png.size_of(data) or (rc["width"], rc["height"])
        ctx.map = {"left": rc["left"], "top": rc["top"], "scale": wh[0] / max(1, rc["width"]),
                   "source": f"window '{win['title']}'"}
        desc = f"window '{win['title']}' ({rc['width']}×{rc['height']} at {rc['left']},{rc['top']})"
    else:
        screens = ctx.desktop.screens()
        idx = int(monitor or 0)
        if not 0 <= idx < len(screens):
            return _fail(f"There's no monitor {idx}; there are {len(screens)}.")
        s = screens[idx]
        ow = min(s["width"], max_width)
        scale = ow / s["width"]
        oh = max(1, round(s["height"] * scale))
        data = ctx.desktop.capture(s["left"], s["top"], s["width"], s["height"], ow, oh)
        wh = (ow, oh)
        ctx.map = {"left": s["left"], "top": s["top"], "scale": scale, "source": f"monitor {idx}"}
        desc = f"monitor {idx} ({s['width']}×{s['height']} at {s['left']},{s['top']})"
    path = _save_shot(ctx, data)
    return _ok(f"Screenshot of {desc}. The image is {wh[0]}×{wh[1]}; give mouse coordinates in this image's pixels.",
               width=wh[0], height=wh[1], saved_to=path, _image=(base64.b64encode(data).decode(), "image/png"))


def screen_info(ctx):
    screens = ctx.desktop.screens()
    cx, cy = ctx.desktop.cursor()
    fg = next((w for w in ctx.desktop.windows() if w.get("focused")), None)
    return _ok(f"{len(screens)} monitor(s). Mouse at {cx},{cy} (screen pixels).", monitors=screens,
               mouse_screen=[cx, cy], mouse_in_screenshot_coords=list(ctx.from_screen(cx, cy)),
               backend=(ctx.desktop.describe() if hasattr(ctx.desktop, "describe") else None),
               coordinate_space=ctx.map["source"], foreground_window=fg["title"] if fg else None)


# ===================================================================== input ====
def _steps(ctx, x0, y0, x1, y1, n=12, delay=0.012):
    for i in range(1, n + 1):
        ctx.link.check()
        ctx.desktop.move(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n)
        time.sleep(delay)


def mouse_move(ctx, x, y):
    sx, sy = ctx.to_screen(x, y)
    cx, cy = ctx.desktop.cursor()
    _steps(ctx, cx, cy, sx, sy, 8, 0.01)
    return _ok(f"Mouse moved to {x},{y} (screen {sx},{sy}).", screen=[sx, sy])


def mouse_click(ctx, x=None, y=None, button="left", clicks=1):
    button = button if button in ("left", "right", "middle") else "left"
    clicks = max(1, min(int(clicks or 1), 3))
    if x is not None and y is not None:
        mouse_move(ctx, x, y)
    for _ in range(clicks):
        ctx.link.check()
        ctx.desktop.button(button, True)
        time.sleep(0.03)
        ctx.desktop.button(button, False)
        time.sleep(0.06)
    pos = ctx.desktop.cursor()
    return _ok(f"{'Double-c' if clicks == 2 else 'C'}licked {button} at screen {pos[0]},{pos[1]}.")


def mouse_drag(ctx, x1, y1, x2, y2, button="left"):
    button = button if button in ("left", "right", "middle") else "left"
    sx1, sy1 = ctx.to_screen(x1, y1)
    sx2, sy2 = ctx.to_screen(x2, y2)
    ctx.link.check()
    ctx.desktop.move(sx1, sy1)
    time.sleep(0.05)
    ctx.desktop.button(button, True)
    try:
        _steps(ctx, sx1, sy1, sx2, sy2, 20, 0.015)
    finally:
        ctx.desktop.button(button, False)
    return _ok(f"Dragged from {x1},{y1} to {x2},{y2}.")


def mouse_scroll(ctx, amount, x=None, y=None, horizontal=False):
    if x is not None and y is not None:
        mouse_move(ctx, x, y)
    amount = max(-50, min(int(amount), 50))
    step = 1 if amount > 0 else -1
    for _ in range(abs(amount)):
        ctx.link.check()
        ctx.desktop.scroll(step, bool(horizontal))
        time.sleep(0.02)
    return _ok(f"Scrolled {amount} notch(es){' sideways' if horizontal else ''}.")


def keyboard_type(ctx, text):
    text = str(text)
    if len(text) > 20000:
        return _fail("That's too much text to type at once (max 20,000 characters).")
    for i in range(0, len(text), 16):
        ctx.link.check()
        ctx.desktop.type_char_units(text[i:i + 16])
        time.sleep(0.01)
    return _ok(f"Typed {len(text):,} character(s).")


def keyboard_hotkey(ctx, keys, presses=1):
    names = [k.strip() for k in re.split(r"\s*\+\s*", str(keys).strip()) if k.strip()] or ([str(keys)] if keys else [])
    if not names:
        return _fail("Which keys?")
    presses = max(1, min(int(presses or 1), 50))
    for _ in range(presses):
        ctx.link.check()
        ctx.desktop.keys(names, True)
        time.sleep(0.03)
        ctx.desktop.keys(names, False)
        time.sleep(0.04)
    return _ok(f"Pressed {'+'.join(names)}" + (f" ×{presses}" if presses > 1 else "") + ".")


# ============================================================ windows / apps ====
def list_windows(ctx):
    wins = ctx.desktop.windows()
    return _ok(f"{len(wins)} window(s) open.", windows=wins)


def focus_window(ctx, window):
    w = _find_window(ctx, window)
    if not w:
        return _fail(f"No open window matches '{window}'.")
    ok = ctx.desktop.focus(w["handle"])
    return (_ok if ok else _fail)(f"{'Switched to' if ok else 'The window manager did not let me bring up'} '{w['title']}'.",
                                  handle=w["handle"])


def open_url(ctx, url):
    u = str(url).strip()
    if not re.match(r"^(https?|mailto):", u, re.I):
        if re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", u, re.I):
            u = "https://" + u
        else:
            return _fail("Only http(s) and mailto links can be opened.")
    ctx.desktop.open(u)
    return _ok(f"Opened {u} in the default browser.")


def open_app(ctx, name, args=""):
    n = str(name).strip().strip('"')
    if not n:
        return _fail("Which app?")
    p = expand(n)
    if re.search(r"[\\/]|^[A-Za-z]:", n) and os.path.exists(p):
        ctx.desktop.open(p, args or "")
        return _ok(f"Opened {p}.")
    apps = ctx.desktop.start_menu_apps()
    ln = n.lower()
    scored = []
    for a in apps:
        an = a["name"].lower()
        score = 100 if an == ln else 80 if an.startswith(ln) else 60 if ln in an else 0
        if score:
            scored.append((score, -len(an), a))
    if scored:
        scored.sort(key=lambda t: (-t[0], -t[1]))
        a = scored[0][2]
        ctx.desktop.open(a["path"], args or "")
        return _ok(f"Opening {a['name']}.", app=a["name"])
    exe = shutil.which(n) or shutil.which(n + ".exe")
    if exe:
        ctx.desktop.open(exe, args or "")
        return _ok(f"Started {exe}.")
    close = sorted(apps, key=lambda a: -len(set(a["name"].lower()) & set(ln)))[:8]
    return _fail(f"I couldn't find an app called '{n}'.", suggestions=[a["name"] for a in close])


TOOLS = {f.__name__: f for f in (fs_list, fs_read, fs_write, fs_move, fs_delete, fs_search, view_image, powershell, shell,
                                 screenshot, screen_info, mouse_move, mouse_click, mouse_drag, mouse_scroll,
                                 keyboard_type, keyboard_hotkey, list_windows, focus_window, open_url, open_app)}


def call(ctx: Ctx, name: str, args: dict | None) -> dict:
    fn = TOOLS.get(name)
    if fn is None:
        return _fail(f"Unknown tool '{name}'.")
    try:
        return fn(ctx, **(args or {}))
    except Aborted:
        try:
            ctx.desktop.release_all()
        except Exception:  # noqa: BLE001
            pass
        return _fail("Stopped: the user pressed the emergency stop. Don't retry unless they ask.")
    except TypeError as e:
        return _fail(f"Bad arguments for {name}: {e}")
    except (ValueError, OSError) as e:
        return _fail(f"{name} failed: {e}")
