"""Детектор ИИ-признаков в коде студента."""

from __future__ import annotations

import re

from forlabs.core.models import Finding

_COMMENT_PREFIX = {
    "bsl": ("//",), "1c": ("//",), "go": ("//", "/*", "*"), "java": ("//", "/*", "*"), "csharp": ("//", "/*", "*", "///"),
    "c#": ("//", "/*", "*"), "cs": ("//", "/*", "*"), "js": ("//", "/*", "*"), "javascript": ("//", "/*", "*"),
    "php": ("//", "#", "/*", "*"), "python": ("#",), "py": ("#",), "css": ("/*", "*"), "html": ("<!--",),
    "sql": ("--",), "bat": ("rem ", "REM ", "::"), "cmd": ("rem ", "REM ", "::"),
}
_DEFAULT_PREFIX = ("//", "#", "/*", "*", "--")
_SEPARATOR = re.compile(r"([=\-*~_#])\1{4,}")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF⭐✅❌✔✖]")
_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
_TODO = re.compile(r"\b(TODO|FIXME|HACK|Fallback|fallback)\b")


def _f(rule: str, msg: str, location: str, text: str = "", suggestion: str | None = None) -> Finding:
    return Finding(stage="antiai", rule_id=rule, severity="error", location=location, text=text,
                   message=msg, suggestion=suggestion)


def scan_code(code: str, language: str, location: str) -> list[Finding]:
    lang = (language or "").lower()
    prefixes = _COMMENT_PREFIX.get(lang, _DEFAULT_PREFIX)
    lines = [ln for ln in code.splitlines() if ln.strip()]
    comment_lines = [ln for ln in lines if ln.strip().startswith(prefixes)]
    out: list[Finding] = []
    if lines and len(lines) >= 4 and len(comment_lines) / len(lines) > 0.25:
        out.append(_f("code.comment_density", f"Слишком много комментариев ({len(comment_lines)} из {len(lines)} строк)",
                      location, suggestion="оставить 1–3 коротких комментария по-русски или ни одного"))
    if lang in ("bsl", "1c"):
        for ln in lines:
            s = ln.strip()
            if s.startswith("//") and _LATIN_WORD.search(s):
                out.append(_f("code.english_comment_bsl", "Английские слова в комментарии 1С", location, s,
                              "комментарий по-русски"))
                break
    for ln in code.splitlines():
        if _TODO.search(ln):
            out.append(_f("code.todo", "Служебные пометки TODO/Fallback в коде", location, ln.strip(), "убрать"))
            break
    for ln in code.splitlines():
        if _SEPARATOR.search(ln):
            out.append(_f("code.separator", "Декоративные разделители в коде или выводе", location, ln.strip(),
                          "убрать разделители"))
            break
    if _EMOJI.search(code):
        out.append(_f("code.emoji", "Эмодзи в коде или выводе программы", location, "", "убрать"))
    return out
