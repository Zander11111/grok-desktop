"""The grok-desktop tool list (names, descriptions, JSON schemas) and how risky each call is.

Pure Python (no Qt, no Windows APIs) so the app and the MCP server share one source of truth.

Categories (what the permission modes look at):
  read            list/read/search files, screenshots, list windows, screen info         -> auto in "safe" mode
  write_new       create a file that doesn't exist yet / move to a free name                -> auto in "safe" mode
  open            open a URL, a Start-menu app by name, focus a window                      -> auto in "safe" mode
  command_read    PowerShell that only reads (Get-*, dir, ipconfig, ...)                    -> auto in "safe" mode
  overwrite       replace or append to an existing file, move onto an existing file         -> asks in "safe" mode
  delete          delete (to the Recycle Bin by default)                                    -> asks in "safe" mode
  command_change  any other PowerShell / starting an .exe or script by path                 -> asks in "safe" mode
  input           mouse and keyboard control                                                -> asks in "safe" mode
"""
from __future__ import annotations

import json
import os
import re
import sys


def plat() -> str:
    """'windows' or 'linux' (GD_PLATFORM overrides: the Windows test-suite runs on Linux with GD_PLATFORM=windows)."""
    return os.environ.get("GD_PLATFORM") or ("windows" if sys.platform == "win32" else "linux")

AUTO_IN_SAFE = {"read", "write_new", "open", "command_read"}
NO_ALLOW_FOR_CHAT = {"delete", "command_change"}   # always asked one by one (in ask/safe modes)
RISK = {"read": "low", "write_new": "medium", "open": "low", "command_read": "low", "overwrite": "high",
        "delete": "high", "command_change": "high", "input": "high"}
INPUT_TOOLS = {"mouse_move", "mouse_click", "mouse_drag", "mouse_scroll", "keyboard_type", "keyboard_hotkey"}


def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


_S, _I, _B, _N = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}, {"type": "number"}
_XY = {"x": {**_N, "description": "X in the coordinates of your latest screenshot (see screenshot)"},
       "y": {**_N, "description": "Y in the coordinates of your latest screenshot"}}
_BTN = {"type": "string", "enum": ["left", "right", "middle"]}

TOOLS: dict[str, tuple[str, dict]] = {
    # ---- files
    "fs_list": ("List a folder: names, sizes, dates. `~` = the user's home folder.",
                _schema({"path": _S, "pattern": {**_S, "description": "glob like *.png"}, "show_hidden": _B}, ["path"])),
    "fs_read": ("Read a text file (UTF-8). For images use view_image.",
                _schema({"path": _S, "offset": {**_I, "description": "character offset"},
                         "max_chars": {**_I, "description": "default 20000"}}, ["path"])),
    "fs_write": ("Write a text file. Refuses to replace an existing file unless overwrite=true (or append=true).",
                 _schema({"path": _S, "content": _S, "overwrite": _B, "append": _B}, ["path", "content"])),
    "fs_move": ("Move or rename a file or folder.",
                _schema({"source": _S, "destination": _S, "overwrite": _B}, ["source", "destination"])),
    "fs_delete": ("Delete a file or folder. Goes to the Recycle Bin unless permanent=true.",
                  _schema({"path": _S, "permanent": _B}, ["path"])),
    "fs_search": ("Find files by name (glob) and optionally by text inside, under a folder.",
                  _schema({"root": _S, "name": {**_S, "description": "glob, e.g. *.docx"},
                           "contains": {**_S, "description": "text to look for inside files"},
                           "limit": _I, "max_depth": _I}, ["root"])),
    "view_image": ("Look at an image file (PNG/JPEG/GIF/BMP/WebP), e.g. one the user attached. Returns the image.",
                   _schema({"path": _S, "max_width": _I}, ["path"])),
    # ---- PowerShell
    "powershell": ("Run a PowerShell command and return its output (stdout, stderr, exit code). Read-only commands "
                   "like Get-ChildItem are fine; anything that changes the system needs the user's OK.",
                   _schema({"command": _S, "cwd": _S, "timeout_s": {**_I, "description": "default 60, max 600"}},
                           ["command"])),
    # ---- screen
    "shell": ("Run a shell command (bash) and return its output (stdout, stderr, exit code). Read-only commands "
              "(ls, cat, df, ps, ...) are allowed more freely than anything that changes the system.",
              _schema({"command": _S, "cwd": {**_S, "description": "working folder, default home"},
                       "timeout_s": {**_I, "description": "default 60, max 600"}}, ["command"])),
    "screenshot": ("Take a screenshot so you can see the screen. target=screen (monitor index, default the primary) "
                   "or window (by title text or handle). Coordinates you pass to the mouse tools afterwards are in "
                   "THIS image's pixel coordinates (they are mapped back to the real screen for you).",
                   _schema({"target": {"type": "string", "enum": ["screen", "window"]}, "monitor": _I,
                            "window": {**_S, "description": "part of the window title, or its handle"},
                            "max_width": {**_I, "description": "default 1600"}})),
    "screen_info": ("Monitors (position/size/scale), the mouse position and the foreground window.", _schema({})),
    # ---- mouse / keyboard
    "mouse_move": ("Move the mouse pointer.", _schema(_XY, ["x", "y"])),
    "mouse_click": ("Click (optionally at x,y first). clicks=2 for double-click.",
                    _schema({**_XY, "button": _BTN, "clicks": _I})),
    "mouse_drag": ("Press, drag from (x1,y1) to (x2,y2), release.",
                   _schema({"x1": _N, "y1": _N, "x2": _N, "y2": _N, "button": _BTN}, ["x1", "y1", "x2", "y2"])),
    "mouse_scroll": ("Scroll the wheel: amount>0 scrolls up, <0 down (in notches). Optional x,y to move there first.",
                     _schema({"amount": _I, **_XY, "horizontal": _B}, ["amount"])),
    "keyboard_type": ("Type text into the focused window (any Unicode). \\n presses Enter.",
                      _schema({"text": _S}, ["text"])),
    "keyboard_hotkey": ("Press a key or key combination, e.g. 'ctrl+s', 'alt+tab', 'win+r', 'enter', 'f5'.",
                        _schema({"keys": _S, "presses": _I}, ["keys"])),
    # ---- windows / apps
    "list_windows": ("List open windows: title, app (process), handle, position, minimized, focused.", _schema({})),
    "focus_window": ("Bring a window to the front (by part of its title or its handle).",
                     _schema({"window": _S}, ["window"])),
    "open_url": ("Open a web address (http/https/mailto) in the default browser.", _schema({"url": _S}, ["url"])),
    "open_app": ("Open an app by its Start-menu name (e.g. 'Notepad', 'Spotify', 'Steam') or a file/folder path "
                 "(opened with its default app).", _schema({"name": _S, "args": _S}, ["name"])),
}

# ----------------------------------------------------------- PowerShell risk --
_RO_CMDLET_PREFIX = ("get-", "test-", "measure-", "select-", "format-", "resolve-", "compare-", "convertto-",
                     "convertfrom-", "group-", "sort-", "where-", "join-", "split-")
_RO_WORDS = {
    "write-output", "write-host", "out-string", "out-host", "foreach-object", "%", "?", "foreach", "where", "select",
    "sort", "ft", "fl", "fw", "measure", "dir", "ls", "gci", "cat", "type", "gc", "echo", "pwd", "gl", "gps", "ps",
    "gsv", "gwmi", "gcim", "gal", "gcm", "help", "man", "whoami", "hostname", "ipconfig", "systeminfo", "tasklist",
    "ver", "ping", "nslookup", "tracert", "netstat", "where.exe", "findstr", "tree", "nvidia-smi", "driverquery",
    "cd", "sl", "set-location", "push-location", "pop-location", "true", "false", "null", "if", "else", "elseif",
    "return",
}
_SH_RO = {
    "ls", "cat", "pwd", "echo", "printf", "whoami", "id", "uname", "hostname", "hostnamectl", "df", "du", "ps", "pgrep",
    "free", "uptime", "date", "env", "printenv", "which", "type", "command", "head", "tail", "wc", "grep", "egrep",
    "rg", "stat", "file", "lsb_release", "lscpu", "lsblk", "lspci", "lsusb", "lsmod", "sort", "uniq", "tree", "ss",
    "nvidia-smi", "xdpyinfo", "xrandr", "wmctrl", "basename", "dirname", "realpath", "readlink", "cd", "true", "false",
    "test", "[", "if", "then", "else", "fi", "for", "do", "done", "while", "sensors", "lsof", "top", "ping", "dig",
    "nslookup", "ip", "locale", "timedatectl", "nmcli", "bluetoothctl", "wpctl", "pactl", "loginctl", "man", "less",
    "more", "cut", "tr", "awk", "jq", "diff", "cmp", "md5sum", "sha256sum", "python3", "xdg-user-dir",
}
_SH_RO_SUB = {"git": {"status", "log", "diff", "show", "branch", "remote"}, "systemctl": {"status", "list-units",
              "is-active", "is-enabled", "list-unit-files"}, "journalctl": None, "dpkg": {"-l", "-L", "-s", "--list"},
              "apt": {"list", "show", "search", "policy"}, "pacman": {"-Q", "-Qi", "-Ql", "-Qs", "-Ss", "-Si", "-Qe"},
              "snap": {"list", "info"}, "flatpak": {"list", "info"}, "pip": {"list", "show"}, "ip": None}
_NATIVE_BAD_ARGS = re.compile(r"/(release|renew|flushdns|registerdns)\b|\s/[fF]\b", re.I)


def classify_command(cmd: str) -> tuple[str, list[str]]:
    """('command_read' | 'command_change', reasons) for a PowerShell / cmd command line. Unknown = change."""
    c = (cmd or "").strip()
    if not c:
        return "command_change", ["empty command"]
    reasons = []
    if re.search(r"(?<![-\w])>|\bOut-File\b|\bTee-Object\b", c, re.I):
        reasons.append("writes output to a file")
    if _NATIVE_BAD_ARGS.search(c) and re.search(r"\b(ipconfig|tasklist|taskkill)\b", c, re.I):
        reasons.append("changes network or process state")
    seps = re.sub(r"\$\(|&&|\|\||[{}();|\n]", "\x00", c)
    for seg in seps.split("\x00"):
        seg = seg.strip()
        m = re.match(r"^\$[\w:]+\s*[-+*/]?=\s*(.*)$", seg)
        if m:
            seg = m.group(1).strip()
        if not seg:
            continue
        if seg.startswith(("&", ". ", ".\\", "./")):
            reasons.append("runs a program or script")
            continue
        if seg[0] in "-$'\"0123456789@,[]+=!<:":
            continue  # a value/parameter, not a command
        word = seg.split()[0].lower()
        base = os.path.basename(word.replace("\\", "/"))
        if base in _RO_WORDS or base.removesuffix(".exe") in _RO_WORDS or word.startswith(_RO_CMDLET_PREFIX):
            if word in ("get-credential",):
                reasons.append("asks for a password")
            continue
        reasons.append(f"runs `{seg.split()[0]}`, which can change your PC")
    return ("command_change" if reasons else "command_read"), reasons


def classify_shell(cmd: str) -> tuple[str, list[str]]:
    """('command_read' | 'command_change', reasons) for a bash command line. Unknown = change."""
    c = (cmd or "").strip()
    if not c:
        return "command_change", ["empty command"]
    reasons = []
    if re.search(r"(?<![-\w&>0-9])>|>>|\btee\b|`|\$\(", c):
        reasons.append("writes output to a file or runs a nested command")
    if re.search(r"\b(sudo|su|doas|pkexec)\b", c):
        reasons.append("runs with administrator rights")
    if re.search(r"\bfind\b.*(-delete|-exec|-ok)\b|\bsed\b.*\s-i|\bpython3?\b.*\s-c\b", c):
        reasons.append("can modify files or run code")
    for seg in re.split(r"&&|\|\||[;|\n(){}]", c):
        seg = seg.strip()
        seg = re.sub(r"^(\w+=\S*\s+)+", "", seg)
        if not seg:
            continue
        words = seg.split()
        w0 = os.path.basename(words[0])
        if w0 in _SH_RO_SUB and _SH_RO_SUB[w0] is not None:
            if not (len(words) > 1 and words[1] in _SH_RO_SUB[w0]):
                reasons.append(f"runs `{w0}`, which can change your computer")
        elif w0 == "journalctl" or w0 in _SH_RO:
            if w0 == "python3" and len(words) > 1 and not words[1].startswith("-"):
                reasons.append("runs a script")
            if w0 == "ip" and len(words) > 1 and words[1] in ("link", "addr", "route", "a", "r", "l") and any(
                    x in words for x in ("add", "del", "set", "flush")):
                reasons.append("changes network settings")
        elif re.match(r"^[A-Za-z_]\w*=", seg):
            continue
        else:
            reasons.append(f"runs `{words[0]}`, which can change your computer")
    return ("command_change" if reasons else "command_read"), reasons


# ------------------------------------------------------------- classify call --
_EXEC_EXT = (".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".com", ".scr", ".lnk", ".reg", ".jar", ".py", ".sh", ".desktop", ".appimage", ".run", ".deb")


def expand(p: str) -> str:
    p = os.path.expandvars(os.path.expanduser(str(p or "").strip().strip('"')))
    return os.path.abspath(p) if p else p


def classify(tool: str, args: dict | None, exists=os.path.exists) -> dict:
    """{'category', 'risk', 'title', 'detail', 'reasons'} for one tool call."""
    a = args or {}
    cat, reasons = "read", []
    title, detail = tool.replace("_", " ").capitalize(), json.dumps(a, ensure_ascii=False)
    if tool in ("fs_list", "fs_read", "fs_search", "view_image"):
        title = {"fs_list": "List a folder", "fs_read": "Read a file", "fs_search": "Search for files",
                 "view_image": "Look at an image"}[tool]
        detail = str(a.get("path") or a.get("root") or "")
        if tool == "fs_search" and (a.get("name") or a.get("contains")):
            detail += f"  (name: {a.get('name') or '*'}" + (f", containing \"{a['contains']}\"" if a.get("contains") else "") + ")"
    elif tool == "fs_write":
        p = expand(a.get("path", ""))
        detail = p
        if exists(p):
            cat = "overwrite"
            title = "Add to an existing file" if a.get("append") else "Replace an existing file"
            reasons.append("the file already exists" + ("" if a.get("append") else " and will be overwritten"))
        else:
            cat, title = "write_new", "Create a file"
        n = len(str(a.get("content", "")))
        detail += f"\n({n:,} characters)"
    elif tool == "fs_move":
        src, dst = expand(a.get("source", "")), expand(a.get("destination", ""))
        detail = f"{src}\n→ {dst}"
        if exists(dst) and not os.path.isdir(dst):
            cat, title = "overwrite", "Move onto an existing file"
            reasons.append("the destination exists and would be replaced")
        else:
            cat, title = "write_new", "Move / rename"
    elif tool == "fs_delete":
        cat, detail = "delete", expand(a.get("path", ""))
        bin_ = "Recycle Bin" if plat() == "windows" else "Trash"
        title = "Delete permanently" if a.get("permanent") else f"Delete (to the {bin_})"
        reasons.append("permanently deletes it" if a.get("permanent") else f"moves it to the {bin_}")
    elif tool == "shell":
        detail = str(a.get("command", ""))
        cat, reasons = classify_shell(detail)
        title = "Run a shell command" + (" (read-only)" if cat == "command_read" else "")
    elif tool == "powershell":
        detail = str(a.get("command", ""))
        cat, reasons = classify_command(detail)
        title = "Run a PowerShell command" + (" (read-only)" if cat == "command_read" else "")
    elif tool in ("screenshot", "screen_info", "list_windows"):
        title = {"screenshot": "Take a screenshot", "screen_info": "Check the screen layout",
                 "list_windows": "List open windows"}[tool]
        detail = f"window: {a['window']}" if a.get("window") else (f"monitor {a['monitor']}" if "monitor" in a else "")
    elif tool in INPUT_TOOLS:
        cat = "input"
        reasons.append("controls your mouse/keyboard")
        if tool == "keyboard_type":
            title, detail = "Type text", str(a.get("text", ""))
        elif tool == "keyboard_hotkey":
            title, detail = "Press keys", str(a.get("keys", "")) + (f" ×{a['presses']}" if a.get("presses", 1) not in (1, None) else "")
        elif tool == "mouse_click":
            n = int(a.get("clicks") or 1)
            title = ("Double-click" if n == 2 else "Click") + ("" if a.get("button", "left") == "left" else f" ({a['button']})")
            detail = f"at {a.get('x')}, {a.get('y')}" if a.get("x") is not None else "where the pointer is"
        elif tool == "mouse_move":
            title, detail = "Move the mouse", f"to {a.get('x')}, {a.get('y')}"
        elif tool == "mouse_drag":
            title, detail = "Drag with the mouse", f"{a.get('x1')}, {a.get('y1')} → {a.get('x2')}, {a.get('y2')}"
        else:
            title, detail = "Scroll", f"{a.get('amount')} notch(es)"
    elif tool == "focus_window":
        cat, title, detail = "open", "Switch to a window", str(a.get("window", ""))
    elif tool == "open_url":
        url = str(a.get("url", ""))
        cat, title, detail = "open", "Open a web page", url
        if not re.match(r"^(https?|mailto):", url, re.I):
            cat = "command_change"
            reasons.append("not a normal web address")
    elif tool == "open_app":
        name = str(a.get("name", ""))
        cat, title, detail = "open", "Open an app", name + (f"  {a['args']}" if a.get("args") else "")
        looks_path = bool(re.search(r"[\\/]|^[A-Za-z]:", name))
        if a.get("args") or (looks_path and name.lower().rstrip('"').endswith(_EXEC_EXT)):
            cat = "command_change"
            reasons.append("starts a program or script directly")
    else:
        cat = "command_change"
        reasons.append("unknown tool")
    return {"category": cat, "risk": RISK[cat], "title": title, "detail": detail, "reasons": reasons}


def descriptors() -> list[dict]:
    out = []
    win = plat() == "windows"
    for name, (desc, schema) in TOOLS.items():
        if name == ("shell" if win else "powershell"):
            continue
        if not win:
            desc = (desc.replace("Recycle Bin", "Trash").replace("'win+r'", "'super+t'")
                    .replace("Start-menu", "app-menu").replace("Start menu", "app menu")
                    .replace("'Notepad', 'Spotify', 'Steam'", "'Firefox', 'Files', 'Terminal'"))
        ro = name in ("fs_list", "fs_read", "fs_search", "view_image", "screenshot", "screen_info", "list_windows")
        out.append({"name": name, "title": name.replace("_", " ").capitalize(), "description": desc,
                    "inputSchema": schema,
                    "annotations": {"readOnlyHint": ro, "destructiveHint": name in ("fs_delete", "powershell", "shell"),
                                    "openWorldHint": name in ("open_url",)}})
    return out
