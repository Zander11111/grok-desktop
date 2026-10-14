"""Turn a conversation into HTML for the chat view (a QTextBrowser).

* Markdown via Python-Markdown with raw HTML switched off (the model's text can't inject HTML).
* Fenced code blocks are cut out first and highlighted with Pygments (inline styles, which Qt's rich text
  understands), each with a "Copy" link (href "copy:<n>"); an unclosed fence while streaming still renders as code.
* Images the user attached are shown inline (file:// URLs); other files as chips.
No Qt imports here so it can be unit-tested on its own.
"""
from __future__ import annotations

import html
import pathlib
import re

import markdown

from .paths import PC_NAME, SHELL_NAME

try:
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import TextLexer, get_lexer_by_name, guess_lexer
    from pygments.util import ClassNotFound
except ImportError:  # pragma: no cover
    highlight = None

ACCENT = "#7C5CFF"
ACCENT2 = "#3D8BFF"
FG, MUTED, CODE_BG, CODE_HEAD, USER_BG = "#E8E6F0", "#8C88A3", "#0B0A10", "#1C1A26", "#272248"
STATUS_WORD = {"pending": "Pending", "in_progress": "Working", "completed": "Done", "failed": "Failed"}
STARTERS = (
    ("error", "Explain this error", "Explain this error:\n\n"),
    ("review", "Review a file", "Review this file. Point out bugs, risks, and anything unclear:\n\n"),
    ("reply", "Draft a reply", "Draft a clear, professional reply to this:\n\n"),
)
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([\w+#.-]*)[^\n]*$")

DOC_CSS = f"""
body {{ color: {FG}; }}
a {{ color: #A996FF; text-decoration: none; }}
code {{ font-family: 'Cascadia Mono', Consolas, 'DejaVu Sans Mono', monospace; background-color: #1E1C29; }}
pre {{ font-family: 'Cascadia Mono', Consolas, 'DejaVu Sans Mono', monospace; margin: 0; }}
th {{ background-color: #1E1C29; padding: 4px 8px; }}
td {{ padding: 2px 6px; }}
h1, h2, h3 {{ color: #FFFFFF; }}
"""


class _NoHtml(markdown.Extension):
    def extendMarkdown(self, md):  # noqa: N802 (Markdown API)
        md.preprocessors.deregister("html_block")
        md.inlinePatterns.deregister("html")


_MD = markdown.Markdown(extensions=["markdown.extensions.tables", "markdown.extensions.sane_lists", _NoHtml()])


_LIST_ITEM = re.compile(r"^\s{0,3}([-*+]|\d+[.)])\s+")


def _loosen_lists(text: str) -> str:
    """Python-Markdown needs a blank line before a list; chat models usually don't leave one."""
    out, prev = [], ""
    for line in text.split("\n"):
        if _LIST_ITEM.match(line) and prev.strip() and not _LIST_ITEM.match(prev) and not prev.startswith((" ", "\t")):
            out.append("")
        out.append(line)
        prev = line
    return "\n".join(out)


def md_to_html(text: str) -> str:
    _MD.reset()
    out = _MD.convert(_loosen_lists(text))
    # Qt rich text: give tables a visible grid
    return out.replace("<table>", '<table border="1" cellspacing="0" cellpadding="4" style="border-color:#34313F">')


def split_fences(text: str) -> list[tuple[str, str, str]]:
    """[(kind, lang, body)], kind = 'md' | 'code'. An unclosed fence runs to the end (streaming)."""
    parts, buf, lang, fence = [], [], "", None
    for line in text.split("\n"):
        m = _FENCE.match(line)
        if fence is None and m:
            if buf:
                parts.append(("md", "", "\n".join(buf)))
            buf, fence, lang = [], m.group(1), m.group(2)
        elif fence is not None and line.strip().startswith(fence[0] * len(fence)) and line.strip().strip(fence[0]) == "":
            parts.append(("code", lang, "\n".join(buf)))
            buf, fence, lang = [], None, ""
        else:
            buf.append(line)
    if fence is not None:
        parts.append(("code", lang, "\n".join(buf)))
    elif buf:
        parts.append(("md", "", "\n".join(buf)))
    return parts


def highlight_code(code: str, lang: str) -> str:
    if highlight is None:
        return html.escape(code)
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    out = highlight(code, lexer, HtmlFormatter(nowrap=True, noclasses=True, style="monokai"))
    return out.rstrip("\n")


class Renderer:
    """Renders messages; remembers each code block's text so the view can copy it (copy:<n>)."""

    def __init__(self):
        self.code_blocks: list[str] = []
        self.message_text: dict[int, str] = {}

    def reset(self) -> None:
        self.code_blocks = []
        self.message_text = {}

    def markdown(self, text: str) -> str:
        out = []
        for kind, lang, body in split_fences(text):
            if kind == "md":
                if body.strip():
                    out.append(md_to_html(body))
                continue
            n = len(self.code_blocks)
            self.code_blocks.append(body)
            out.append(
                f'<table width="100%" cellspacing="0" cellpadding="0" style="margin-top:6px; margin-bottom:6px">'
                f'<tr><td bgcolor="{CODE_HEAD}" style="padding:4px 10px; color:{MUTED}">{html.escape(lang or "code")}</td>'
                f'<td bgcolor="{CODE_HEAD}" align="right" style="padding:4px 10px"><a href="copy:{n}">Copy</a></td></tr>'
                f'<tr><td colspan="2" bgcolor="{CODE_BG}" style="padding:8px 10px"><pre>{highlight_code(body, lang)}</pre>'
                f'</td></tr></table>')
        return "".join(out)

    def attachments(self, atts: list[dict]) -> str:
        out = []
        for a in atts or []:
            p = a.get("path", "")
            if a.get("kind") == "image" and p:
                w = int(a.get("width") or 320)
                uri = pathlib.Path(p).as_uri() if pathlib.Path(p).is_absolute() else p
                out.append(f'<img src="{html.escape(uri)}" width="{min(w, 360)}"> ')
            else:
                name = html.escape(a.get("name", p))
                out.append(f'<span style="background-color:#1E1C29; color:{FG}">&nbsp;{name}&nbsp;</span> ')
        return "<p>" + "".join(out) + "</p>" if out else ""

    def message(self, m: dict) -> str:
        role, text, mid = m.get("role"), m.get("text", ""), m.get("id", 0)
        if role == "user":
            self.message_text[mid] = text
            body = html.escape(text).replace("\n", "<br>")
            return (f'<table width="100%" cellspacing="0" cellpadding="0" style="margin-top:18px; margin-bottom:8px"><tr>'
                    f'<td width="30%"></td>'
                    f'<td bgcolor="{USER_BG}" style="padding:12px 14px">{self.attachments(m.get("attachments"))}{body}</td>'
                    f'</tr></table>')
        if role == "assistant":
            self.message_text[mid] = text
            cursor = ' <span style="color:%s">▍</span>' % ACCENT if m.get("streaming") else ""
            return (f'<p style="margin-top:18px; margin-bottom:4px"><b><span style="color:{ACCENT}">Grok</span></b></p>'
                    f'{self.markdown(text)}{cursor}')
        if role == "thought":
            short = " ".join(text.split())
            short = short if len(short) < 400 else short[:400] + "…"
            return f'<p style="color:{MUTED}; margin-top:8px; margin-bottom:2px"><i>{html.escape(short)}</i></p>'
        if role == "tool":
            meta = m.get("meta") or {}
            word = STATUS_WORD.get(meta.get("status", ""), "Working")
            return (f'<p style="color:{MUTED}; margin-top:2px; margin-bottom:2px">'
                    f'{html.escape(word)} · {html.escape(text)}</p>')
        # note
        kind = (m.get("meta") or {}).get("kind", "")
        color = "#FF7A90" if kind == "error" else MUTED
        return f'<p align="center" style="color:{color}">{html.escape(text)}</p>'

    def conversation(self, messages: list[dict]) -> str:
        self.reset()
        return "".join(self.message(m) for m in messages)


def starter_text(key: str) -> str:
    for name, _label, text in STARTERS:
        if name == key:
            return text
    return ""


def welcome_html() -> str:
    cells = "".join(
        f'<td bgcolor="#1C1A26" style="padding:8px 14px"><a href="prompt:{key}">{html.escape(label)}</a></td>'
        for key, label, _text in STARTERS)
    return (f'<br><br><p align="center" style="font-size:26px"><b><span style="color:{ACCENT}">Grok</span>'
            f'<span style="color:{ACCENT2}"> Desktop</span></b></p>'
            f'<p align="center" style="color:{MUTED}">Ask anything. Attach files or images, or drag them onto the window.<br>'
            f'Grok can also use your {PC_NAME} (files, {SHELL_NAME}, screen, mouse and keyboard) — it asks first.</p>'
            f'<table align="center" cellspacing="8" cellpadding="0"><tr>{cells}</tr></table>')
