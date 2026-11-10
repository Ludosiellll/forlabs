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
    if lang in ("bsl", "1c"):
        out += _bsl_compile_checks(code, location)
    return out


_BSL_SERVER = re.compile(r"\b(Справочники|Документы|РегистрыСведений|РегистрыНакопления|Константы)\.\w+\.\w+\(|"
                         r"\.Записать\(|\bНовый\s+Запрос\b|\.ПолучитьОбъект\(\)", re.I)
_BSL_PROC = re.compile(r"^\s*(Процедура|Функция)\b", re.I)
_BSL_END = re.compile(r"^\s*(КонецПроцедуры|КонецФункции)\b", re.I)


def _bsl_compile_checks(code: str, location: str) -> list[Finding]:
    """То, что не скомпилируется в 1С: \\" в строке (в 1С кавычка удваивается) и серверные вызовы в &НаКлиенте."""
    out: list[Finding] = []
    for ln in code.splitlines():
        if '\\"' in ln:
            out.append(_f("code.bsl_backslash_quote", "В 1С кавычка внутри строки удваивается (\"\"), а не \\\"",
                          location, ln.strip(), 'заменить \\" на ""'))
            break
    directive, on_client = "", False
    for ln in code.splitlines():
        s = ln.strip()
        if s.startswith("&"):
            directive = s.lower()
            continue
        if _BSL_PROC.match(s):
            on_client, directive = directive.startswith("&наклиенте"), ""
            continue
        if _BSL_END.match(s):
            on_client = False
            continue
        if on_client and _BSL_SERVER.search(s):
            out.append(_f("code.bsl_server_on_client", "Обращение к базе в процедуре &НаКлиенте — так в 1С нельзя",
                          location, s, "вынести в процедуру &НаСервере и вызвать её с клиента"))
            break
    return out


_JAVA_CLASS = re.compile(r"\bpublic\s+(?:final\s+|abstract\s+)*class\s+(\w+)")
_JAVA_FILE = re.compile(r"\b(\w+)\.java\b")


def java_class_mismatch(code: str, text: str) -> str | None:
    """Публичный класс назван не так, как файл .java из текста задания — javac такое не скомпилирует."""
    m = _JAVA_CLASS.search(code)
    files = set(_JAVA_FILE.findall(text))
    if m and files and m.group(1) not in files:
        return f"public class {m.group(1)}, а в тексте файл {sorted(files)[0]}.java"
    return None
