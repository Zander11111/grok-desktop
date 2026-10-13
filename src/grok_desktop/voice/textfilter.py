"""Turns streamed markdown from grok into short sentences that are nice to hear: skips code blocks, tables, tool noise,
URLs and markdown symbols. Pure Python, no Qt."""
from __future__ import annotations

import re

CODE_NOTE = "I've put the code in the chat."
_END = re.compile(r"""[.!?…]+["')\]]*(?=\s)""")
_URL = re.compile(r"https?://\S+|www\.\S+")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_INLINE = re.compile(r"`([^`]*)`")
_EMOJI = re.compile("[\U0001F000-\U0001FFFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")
_PATHLIKE = re.compile(r"[/\\_=<>{}\[\]()$#*]|\w\.\w")


def clean(text: str) -> str:
    t = _LINK.sub(r"\1", text)
    t = _URL.sub("a link", t)

    def inline(m):
        s = m.group(1)
        return s if (len(s) <= 18 and not _PATHLIKE.search(s)) else ""   # short plain words are read, commands/paths skipped
    t = _INLINE.sub(inline, t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t)
    t = re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s+", "", t)
    t = re.sub(r"(\*\*|__|\*|~~|`)", "", t)
    t = _EMOJI.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def split_long(s: str, limit: int = 260) -> list[str]:
    out = []
    while len(s) > limit:
        cut = max(s.rfind(", ", 0, limit), s.rfind("; ", 0, limit), s.rfind(" ", 0, limit))
        cut = cut if cut > 40 else limit
        out.append(s[:cut + 1].strip())
        s = s[cut + 1:].strip()
    if s:
        out.append(s)
    return out


class SpeechFilter:
    def __init__(self, min_len: int = 14):
        self.min_len = min_len
        self.reset()

    def reset(self) -> None:
        self.buf = ""
        self.in_code = False
        self.announced = False
        self.carry = ""

    # -- internals
    def _emit(self, text: str, out: list[str]) -> None:
        c = clean(text)
        if not re.search(r"\w", c):
            return
        for piece in split_long(c):
            piece = (self.carry + " " + piece).strip() if self.carry else piece
            self.carry = ""
            if len(piece) < self.min_len:
                self.carry = piece
            else:
                out.append(piece)

    def _line(self, line: str, out: list[str]) -> None:
        st = line.strip()
        if st.startswith("```") or st.startswith("~~~"):
            self.in_code = not self.in_code
            if self.in_code and not self.announced:
                self.announced = True
                self._flush_carry(out)
                out.append(CODE_NOTE)
            return
        if self.in_code or not st:
            return
        if st.startswith("|") or re.fullmatch(r"[-|:+ =]{3,}", st):
            return
        for part in re.split(r"(?<=[.!?…])[\"')\]]*\s+", st):
            self._emit(part, out)

    def _flush_carry(self, out: list[str]) -> None:
        if self.carry:
            out.append(self.carry)
            self.carry = ""

    def feed(self, text: str) -> list[str]:
        out: list[str] = []
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self._line(line, out)
        if not self.in_code and not self.buf.lstrip().startswith(("`", "~")):
            last = None
            for m in _END.finditer(self.buf):
                last = m
            if last:
                head, self.buf = self.buf[:last.end()], self.buf[last.end():].lstrip(" ")
                self._line(head, out)
        return out

    def step_break(self) -> list[str]:
        """A tool call happened: finish what was being said, say nothing about the tool."""
        return self.flush()

    def flush(self) -> list[str]:
        out: list[str] = []
        if self.buf and not self.in_code:
            self._line(self.buf, out)
        self.buf = ""
        self._flush_carry(out)
        self.in_code = False
        return out
