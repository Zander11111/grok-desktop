"""Conversation history in SQLite (%APPDATA%\\GrokDesktop\\history.sqlite3)."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
  session_id TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', synced INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', attachments TEXT NOT NULL DEFAULT '[]',
  meta TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conv_id, id);
"""
# roles: user | assistant | thought | tool | note (system notes: stopped, permission decisions, errors)


class Store:
    def __init__(self, path: str):
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ---------------------------------------------------------- conversations --
    def create(self, title: str = "New chat") -> str:
        cid = uuid.uuid4().hex[:12]
        now = time.time()
        self.db.execute("INSERT INTO conversations(id,title,created,updated) VALUES(?,?,?,?)", (cid, title, now, now))
        self.db.commit()
        return cid

    def get(self, cid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return dict(r) if r else None

    def list(self, query: str = "") -> list[dict]:
        q = query.strip()
        if not q:
            rows = self.db.execute("SELECT * FROM conversations ORDER BY updated DESC").fetchall()
        else:
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            rows = self.db.execute(
                "SELECT * FROM conversations c WHERE c.title LIKE ? ESCAPE '\\' OR EXISTS ("
                " SELECT 1 FROM messages m WHERE m.conv_id=c.id AND m.role IN ('user','assistant')"
                " AND m.text LIKE ? ESCAPE '\\') ORDER BY updated DESC", (like, like)).fetchall()
        return [dict(r) for r in rows]

    def rename(self, cid: str, title: str) -> None:
        self.db.execute("UPDATE conversations SET title=? WHERE id=?", (title.strip() or "Untitled", cid))
        self.db.commit()

    def delete(self, cid: str) -> None:
        self.db.execute("DELETE FROM conversations WHERE id=?", (cid,))
        self.db.commit()

    def set_session(self, cid: str, session_id: str, model: str = "") -> None:
        self.db.execute("UPDATE conversations SET session_id=?, model=? WHERE id=?", (session_id, model, cid))
        self.db.commit()

    def touch(self, cid: str) -> None:
        self.db.execute("UPDATE conversations SET updated=? WHERE id=?", (time.time(), cid))
        self.db.commit()

    # --------------------------------------------------------------- messages --
    def add(self, cid: str, role: str, text: str = "", attachments: list | None = None, meta: dict | None = None) -> int:
        cur = self.db.execute("INSERT INTO messages(conv_id,role,text,attachments,meta,created) VALUES(?,?,?,?,?,?)",
                              (cid, role, text, json.dumps(attachments or []), json.dumps(meta or {}), time.time()))
        self.db.execute("UPDATE conversations SET updated=? WHERE id=?", (time.time(), cid))
        self.db.commit()
        return cur.lastrowid

    def update(self, mid: int, text: str | None = None, meta: dict | None = None) -> None:
        if text is not None:
            self.db.execute("UPDATE messages SET text=? WHERE id=?", (text, mid))
        if meta is not None:
            self.db.execute("UPDATE messages SET meta=? WHERE id=?", (json.dumps(meta), mid))
        self.db.commit()

    def messages(self, cid: str) -> list[dict]:
        out = []
        for r in self.db.execute("SELECT * FROM messages WHERE conv_id=? ORDER BY id", (cid,)).fetchall():
            d = dict(r)
            d["attachments"] = json.loads(d["attachments"] or "[]")
            d["meta"] = json.loads(d["meta"] or "{}")
            out.append(d)
        return out

    def context_text(self, cid: str, max_chars: int = 12000, skip_newest_user: bool = True) -> str:
        """Recent user/assistant turns as plain text, newest kept, for re-sending context to a fresh grok session.
        skip_newest_user: leave out the message being sent right now."""
        parts, total = [], 0
        spoken = [m for m in self.messages(cid) if m["role"] in ("user", "assistant") and m["text"].strip()]
        if skip_newest_user and spoken and spoken[-1]["role"] == "user":
            spoken = spoken[:-1]
        for m in reversed(spoken):
            who = "User" if m["role"] == "user" else "Assistant"
            names = [a.get("name", "") for a in m["attachments"]]
            line = f"{who}: {m['text'].strip()}" + (f"\n[attached: {', '.join(names)}]" if names else "")
            if total + len(line) > max_chars:
                if not parts:
                    parts.append(line[-max_chars:])
                break
            parts.append(line)
            total += len(line)
        return "\n\n".join(reversed(parts))
