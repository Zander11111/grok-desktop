"""Decide whether an action grok wants to take may run silently or needs the user's OK.

Shapes/logic reused from gridos-companion/permissions.py (resolve_tool, pick_option, the never-pick list);
categories come from mcp/catalog.py so the app and the MCP server agree.

Modes (Settings > Permissions):
  ask   "Ask every time" (default): everything asks, except what you allowed "for this chat".
  safe  "Auto-allow safe actions": reading/listing/searching files, screenshots, listing/focusing windows,
        opening apps/URLs, creating NEW files and read-only PowerShell run silently. Still asks before deleting,
        overwriting, commands that change the system, and any mouse/keyboard control.
  all   "Allow everything": nothing asks (one-time warning first). The emergency stop still works.
Delete and system-changing commands never get an "Allow for this chat" button.
"""
from __future__ import annotations

import json
import os
import re

from .mcp import SERVER_NAME
from .mcp.catalog import AUTO_IN_SAFE, NO_ALLOW_FOR_CHAT, RISK, classify, classify_command, expand

MODES = ("ask", "safe", "all")
MODE_LABELS = {"ask": "Ask every time", "safe": "Auto-allow safe actions", "all": "Allow everything"}
NEVER_PICK_OPTION_IDS = {"enable-always-approve", "always-allow", "always-approve"}


def normalize_mode(mode) -> str:
    return mode if mode in MODES else "ask"


def _mcp_from_pretty(title: str) -> str | None:
    """grok renders MCP tools as '(Server) Action Name'; turn it back into server__action_name."""
    m = re.match(r"^\((.+?)\)\s+(.+)$", title or "")
    if not m:
        return None
    return f"{m.group(1).strip().lower()}__{m.group(2).strip().lower().replace(' ', '_')}"


def resolve_tool(tool_call: dict, options: list[dict] | None = None) -> tuple[str, object]:
    raw = tool_call.get("rawInput")
    name, args = "", raw
    if isinstance(raw, dict) and isinstance(raw.get("tool_name"), str):  # grok's MCP meta-tool shape
        name, args = raw["tool_name"], raw.get("tool_input", raw.get("arguments", {}))
    if not name and isinstance(tool_call.get("name"), str):
        name = tool_call["name"]
    meta = tool_call.get("_meta") or {}
    if not name and isinstance(meta, dict):
        for k in ("toolName", "tool_name", "x.ai/toolName"):
            if isinstance(meta.get(k), str):
                name = meta[k]
                break
    for o in options or []:
        om = o.get("_meta") or {}
        if isinstance(om, dict) and isinstance(om.get("tool_name"), str) and "__" in om["tool_name"]:
            name = name if (name and "__" in name) else om["tool_name"]
    if not name or name == "use_tool":
        name = _mcp_from_pretty(tool_call.get("title", "")) or name or tool_call.get("title", "")
    return name, args


def _command_of(args) -> str | None:
    if isinstance(args, dict):
        for k in ("command", "cmd", "script"):
            v = args.get(k)
            if isinstance(v, list):
                return " ".join(map(str, v))
            if isinstance(v, str):
                return v
    return None


def _paths_of(tc: dict, args) -> list[str]:
    paths = [l.get("path") for l in tc.get("locations") or [] if isinstance(l, dict) and l.get("path")]
    if isinstance(args, dict):
        for k in ("path", "file_path", "target_file", "filePath", "file", "destination", "target", "new_path"):
            v = args.get(k)
            if isinstance(v, str) and v not in paths:
                paths.append(v)
    for c in tc.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "diff" and c.get("path") and c["path"] not in paths:
            paths.append(c["path"])
    return paths


def _a(category, title, detail, reasons=None, key="", tool="", args=None, server="") -> dict:
    return {"category": category, "risk": RISK.get(category, "medium"), "title": title, "detail": detail,
            "reasons": reasons or [], "key": key or f"{category}:{tool}", "tool": tool, "server": server,
            "args": args if isinstance(args, dict) else None,
            "allow_chat_ok": category not in NO_ALLOW_FOR_CHAT}


def assess_mcp(tool: str, args: dict | None, exists=os.path.exists) -> dict:
    c = classify(tool, args or {}, exists)
    return _a(c["category"], c["title"], c["detail"], c["reasons"], key=f"{c['category']}:{tool}", tool=tool,
              args=args or {}, server=SERVER_NAME)


def assess(tool_call: dict, options: list[dict] | None = None, exists=os.path.exists) -> dict:
    """Assessment for an ACP session/request_permission (grok's own tools or MCP tools)."""
    name, args = resolve_tool(tool_call, options)
    kind = (tool_call.get("kind") or "other").lower()
    title = tool_call.get("title") or name or "Unknown action"
    args_text = json.dumps(args, ensure_ascii=False, indent=1) if isinstance(args, (dict, list)) else str(args or "")

    if "__" in name:
        server, tool = name.split("__", 1)
        if server == SERVER_NAME:
            return assess_mcp(tool, args if isinstance(args, dict) else {}, exists)
        return _a("command_change", f"Use tool '{tool}' from '{server}'", args_text, ["a tool from another MCP server"],
                  key=f"mcp:{name}", tool=name, server=server) | {"allow_chat_ok": True}

    cmd = _command_of(args)
    if kind == "execute" or cmd is not None:
        cmd = cmd if cmd is not None else title
        cat, reasons = classify_command(cmd)
        return _a(cat, "Run a command" + (" (read-only)" if cat == "command_read" else ""), cmd,
                  reasons or ["only reads information"], tool="shell")

    paths = _paths_of(tool_call, args)
    if kind == "delete":
        return _a("delete", "Delete a file", "\n".join(paths) or args_text, ["deletes files"], tool="delete")
    if kind in ("edit", "move") or (paths and kind not in ("read", "search", "fetch", "think")):
        existing = [p for p in paths if exists(expand(p))]
        if existing and kind != "move":
            return _a("overwrite", "Change an existing file", "\n".join(paths),
                      [f"{os.path.basename(p)} already exists" for p in existing], tool="edit")
        return _a("write_new", "Create a file" if kind == "edit" else "Move/rename a file",
                  "\n".join(paths) or args_text, tool=kind)
    if kind in ("read", "search", "fetch", "think"):
        return _a("read", title, "\n".join(paths) or args_text, tool=kind)
    return _a("command_change", title, args_text, ["an action Grok Desktop doesn't recognise"], key=f"other:{name}",
              tool=name) | {"allow_chat_ok": True}


def decide(a: dict, mode: str, chat_allowed: set[str] | None = None) -> str:
    """'allow' silently or 'ask'."""
    mode = normalize_mode(mode)
    if mode == "all":
        return "allow"
    if mode == "safe" and a["category"] in AUTO_IN_SAFE:
        return "allow"
    if a.get("allow_chat_ok") and chat_allowed and a.get("key") in chat_allowed:
        return "allow"
    return "ask"


def pick_option(options: list[dict], choice: str) -> dict:
    """Map 'allow' | 'deny' onto grok's options -> ACP outcome. Never picks broad always-approve options."""
    usable = [o for o in options if o.get("optionId") not in NEVER_PICK_OPTION_IDS]
    order = {"allow": ["allow_once"], "deny": ["reject_once", "reject_always"]}[choice]
    for kind in order:
        for o in usable:
            if o.get("kind") == kind:
                return {"outcome": "selected", "optionId": o["optionId"]}
    if choice == "allow":
        for o in usable:
            if o.get("kind") == "allow_always":
                return {"outcome": "selected", "optionId": o["optionId"]}
    return {"outcome": "cancelled"}


def canon_args(args) -> str:
    return json.dumps(args if isinstance(args, dict) else {}, sort_keys=True, ensure_ascii=False)
