"""Текст методички из PDF, DOCX, изображений и обычного текста."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
TEXT_EXT = {".txt", ".md"}


@dataclass
class SourceText:
    text: str
    pages: int = 1
    warnings: list[str] = field(default_factory=list)


_HYPHEN_BREAK = re.compile(r"([А-Яа-яЁёA-Za-z]+)-[ \t]*\n\s*([а-яёa-z][А-Яа-яЁёA-Za-z]*)")
_LATIN = re.compile(r"[A-Za-z]")


def _known(word: str) -> bool:
    from forlabs.report.morph import is_known_word

    return is_known_word(word)


def _join_break(m: re.Match) -> str:
    left, right = m.group(1), m.group(2)
    if _LATIN.search(left) or _LATIN.search(right):
        return f"{left}-{right}"  # e-mail, Wi-Fi
    if _known(left + right):
        return left + right  # перенос по слогам: «програм-мы»
    if _known(f"{left}-{right}") or (_known(left) and _known(right)):
        return f"{left}-{right}"  # настоящий дефис на конце строки: «веб-страницу»
    return left + right


def join_hyphenation(text: str) -> str:
    """Склеивает перенос слова на конце строки, сохраняя дефис составных слов."""
    return _HYPHEN_BREAK.sub(_join_break, text)


CONTROL_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\ud800-\udfff]")


def read_text_file(path: Path) -> str:
    """.txt из Блокнота: UTF-8 (с BOM или без), UTF-16 или ANSI Windows (cp1251)."""
    data = Path(path).read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace")


def normalize(text: str) -> str:
    text = CONTROL_CHARS.sub("", text.replace("\x0c", "\n").replace("\x0b", "\n"))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub("\u00ad[ \t]*\n[ \t]*", "", text)  # мягкий перенос на конце строки — слово продолжается
    text = text.replace("\u00ad", "").replace("\u00a0", " ")
    text = join_hyphenation(text)
    lines = [ln.rstrip() for ln in text.split("\n")]
    out = "\n".join(lines)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


_PAGE_NUMBER = re.compile(r"(?:[-–—]\s*)?\d{1,3}(?:\s*[-–—])?|(?:стр\.?|страница)\s*\d{1,3}", re.IGNORECASE)


def _strip_page_furniture(pages: list[str]) -> list[str]:
    """Убирает номера страниц и колонтитулы: только первые и последние строки страницы.

    Колонтитул — строка, которая стоит первой (или последней) на большинстве страниц.
    Числа внутри страницы (ячейки таблицы вариантов) не трогаются."""
    split = [[ln.strip() for ln in p.split("\n")] for p in pages]
    split = [[ln for ln in lines if ln] for lines in split]

    def repeated(edge: int) -> set[str]:
        if len(split) < 2:
            return set()
        counts: dict[str, int] = {}
        for lines in split:
            if lines:
                counts[lines[edge]] = counts.get(lines[edge], 0) + 1
        return {ln for ln, c in counts.items() if c >= 2 and c * 2 >= len(split) and not _PAGE_NUMBER.fullmatch(ln)}

    heads, foots = repeated(0), repeated(-1)
    out = []
    for lines in split:
        changed = True
        while lines and changed:
            changed = False
            if lines and (_PAGE_NUMBER.fullmatch(lines[0]) or lines[0] in heads):
                lines, changed = lines[1:], True
            if lines and (_PAGE_NUMBER.fullmatch(lines[-1]) or lines[-1] in foots):
                lines, changed = lines[:-1], True
        out.append("\n".join(lines))
    return out


def _pdf(path: Path) -> SourceText:
    import pymupdf

    parts, warnings = [], []
    with pymupdf.open(path) as doc:
        pages = doc.page_count
        empty = 0
        for page in doc:
            t = page.get_text("text")
            if len(t.strip()) < 20:
                empty += 1
            parts.append(t)
    parts = _strip_page_furniture(parts)
    if empty and empty == pages:
        warnings.append("В PDF нет текстового слоя (скан). Нужен Tesseract или вставь текст вручную.")
    elif empty:
        warnings.append(f"{empty} стр. без текста (картинки) — проверь, не там ли таблица вариантов.")
    return SourceText(normalize("\n".join(parts)), pages, warnings)


def _docx(path: Path) -> SourceText:
    from docx import Document
    from docx.oxml.ns import qn

    d = Document(str(path))
    lines: list[str] = []
    for el in d.element.body.iterchildren():
        if el.tag == qn("w:p"):
            lines.append("".join(t.text or "" for t in el.iter(qn("w:t"))))
        elif el.tag == qn("w:tbl"):
            for tr in el.iter(qn("w:tr")):
                cells = ["".join(t.text or "" for t in tc.iter(qn("w:t"))).strip() for tc in tr.iter(qn("w:tc"))]
                lines.append(" | ".join(cells))
    return SourceText(normalize("\n".join(lines)))


OCR_TIMEOUT = 180  # секунд на одну картинку


TESSERACT_WINDOWS = [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                     r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]


def find_tesseract(configured: str | None = None) -> str | None:
    """Путь из настроек, PATH или обычная папка установки (установщик не добавляет Tesseract в PATH)."""
    import sys

    if configured and Path(configured.strip().strip('"')).exists():
        return configured.strip().strip('"')
    found = shutil.which("tesseract")
    if found:
        return found
    if sys.platform == "win32":
        return next((c for c in TESSERACT_WINDOWS if Path(c).exists()), None)
    return None


def _image(path: Path, tesseract: str | None) -> SourceText:
    exe = find_tesseract(tesseract)
    if not exe:
        return SourceText("", warnings=["Для распознавания картинок нужен Tesseract (rus). Установи его или вставь текст."])
    try:
        res = subprocess.run([exe, str(path), "-", "-l", "rus+eng"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=OCR_TIMEOUT,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # без мелькающего окна
    except subprocess.TimeoutExpired:
        return SourceText("", warnings=["Tesseract читал картинку слишком долго и был остановлен — вставь текст "
                                        "вручную или загрузи PDF."])
    if res.returncode != 0:
        return SourceText("", warnings=[f"Tesseract не смог прочитать картинку: {res.stderr.strip()[:200]}"])
    return SourceText(normalize(res.stdout), warnings=["Текст распознан с картинки — проверь его на экране подтверждения."])


def extract_text(path: Path, tesseract: str | None = None) -> SourceText:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf(path)
    if ext == ".docx":
        return _docx(path)
    if ext in TEXT_EXT:
        return SourceText(normalize(read_text_file(path)))
    if ext in IMAGE_EXT:
        return _image(path, tesseract)
    raise ValueError(f"Формат {ext or 'без расширения'} не поддерживается: нужен PDF, DOCX, TXT или картинка")


def extract_many(paths: list[Path], tesseract: str | None = None) -> SourceText:
    parts, warnings, pages = [], [], 0
    for p in paths:
        s = extract_text(p, tesseract)
        parts.append(s.text)
        warnings.extend(s.warnings)
        pages += s.pages
    return SourceText(normalize("\n\n".join(parts)), pages, warnings)
