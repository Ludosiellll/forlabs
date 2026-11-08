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


def normalize(text: str) -> str:
    text = text.replace("­", "").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"([А-Яа-яЁёA-Za-z])-\s*\n\s*([а-яёa-z])", r"\1\2", text)  # перенос слова
    lines = [ln.rstrip() for ln in text.split("\n")]
    lines = [ln for ln in lines if not re.fullmatch(r"\s*\d{1,3}\s*", ln)]  # номера страниц
    out = "\n".join(lines)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


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


def _image(path: Path, tesseract: str | None) -> SourceText:
    exe = tesseract or shutil.which("tesseract")
    if not exe:
        return SourceText("", warnings=["Для распознавания картинок нужен Tesseract (rus). Установи его или вставь текст."])
    res = subprocess.run([exe, str(path), "-", "-l", "rus+eng"], capture_output=True, text=True, encoding="utf-8")
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
        return SourceText(normalize(path.read_text(encoding="utf-8", errors="replace")))
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
