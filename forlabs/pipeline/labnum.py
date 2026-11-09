"""Номер лабы по имени файла или заголовку в тексте — без модели. Нужен, когда в одной форме методички разных лаб
(«ЛР19 библиотеки.docx», «ЛР20 файлы.docx»): каждая становится своей лабой, а не склеивается в одну."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from forlabs.core.store import safe_file_name
from forlabs.ingest.extract import IMAGE_EXT, extract_text
from forlabs.pipeline.memory import norm_number, split_labs

MIN_LAB_CHARS = 200  # лаба в тексте — заголовок и задания; строка оглавления или ссылка короче
PASTED_NAME = "текст.txt"  # так вставленный текст лежит в source/

_L = "а-яёa-z"
# «ЛР19», «ЛР 20», «лаб_21», «Лабораторная работа 3», «Лаб.раб.7», «ПР-4», «lab05»; «ЛР 1-5», «ЛР 1, 2» — не номер
_NAME = re.compile(
    rf"(?<![{_L}])(?:лаб(?:ораторн[{_L}]*|[аы])?|практи(?:ческ|к)[{_L}]*|прак|лр|пр|lab|lr|pr)(?![{_L}])"
    rf"(?:[\s_.\-]*(?:работ[{_L}]*|раб|заняти[{_L}]*)(?![{_L}]))?"
    r"[\s_.\-]*(?:№|no\.?|#)?[\s_.\-]*(\d{1,3}(?:\.\d{1,2})?)(?!\d)"
    r"(?![\s_]*(?:[-–—,+]|и(?![а-яё]))[\s_]*\d)",
    re.IGNORECASE)


def shown_names(names: list[str]) -> list[str]:
    """Имена файлов методички для страницы: вставленный текст — словами, а не «текст.txt»."""
    return ["вставленный текст" if n == PASTED_NAME else n for n in names]


def number_in_name(name: str) -> str | None:
    """Номер лабы в имени файла; нет его или их несколько («ЛР1 и ЛР2») — None."""
    found = {norm_number(m.group(1)) for m in _NAME.finditer(Path(name or "").stem)}
    return found.pop() if len(found) == 1 else None


def labs_in_text(text: str) -> list[str]:
    """Лабы, у которых в тексте есть заголовок «Лабораторная работа №N» и тело, — по порядку (оглавление не в счёт)."""
    return [n for n, body in split_labs(text or "").items() if len(body) >= MIN_LAB_CHARS]


def number_in_text(text: str) -> str | None:
    """Номер лабы по заголовку в тексте. Сборник из нескольких лаб — None: какая нужна, решит разбор."""
    labs = labs_in_text(text)
    if labs:
        return labs[0] if len(labs) == 1 else None
    heads = list(split_labs(text or ""))
    return heads[0] if len(heads) == 1 else None


@dataclass
class Part:
    """Файл методички (или вставленный текст): номер лабы и лабы в его тексте."""
    name: str
    data: bytes
    number: str | None = None
    labs: list[str] = field(default_factory=list)
    photo: bool = False


def read_parts(files: list[tuple[str, bytes]], pasted_text: str = "") -> list[Part]:
    """Номер — из имени, иначе из заголовка в тексте. Фото не распознаются (это долго): у них номер только из имени."""
    items = list(files) + ([(PASTED_NAME, pasted_text.encode("utf-8"))] if pasted_text.strip() else [])
    parts = []
    with tempfile.TemporaryDirectory(prefix="forlabs-") as tmp:
        for i, (name, data) in enumerate(items):
            part = Part(name=name, data=data, number=number_in_name(name),
                        photo=Path(name or "").suffix.lower() in IMAGE_EXT)
            if not part.photo:
                f = Path(tmp) / f"{i}_{safe_file_name(Path(name or '').name or 'file')}"
                try:
                    f.write_bytes(data)
                    text = extract_text(f).text
                except Exception:  # битый файл или чужой формат — без номера; ошибку покажет разбор
                    text = ""
                part.labs = labs_in_text(text)
                if part.number is None:
                    part.number = number_in_text(text)
            parts.append(part)
    return parts


def _order(n: str):
    try:
        return (0, float(n))
    except ValueError:
        return (1, n)


def split_by_lab(files: list[tuple[str, bytes]], pasted_text: str = "") -> list[tuple[str, list[tuple[str, bytes]]]]:
    """Методички разных лаб → [(номер, файлы этой лабы)]. Пусто — это одна лаба: файл один, номер у всех один,
    есть фото страниц или часть без номера (приложение, сборник)."""
    if len(files) < 2:
        return []
    parts = read_parts(files, pasted_text)
    if any(p.photo or p.number is None for p in parts):
        return []
    groups: dict[str, list[tuple[str, bytes]]] = {}
    for p in parts:
        groups.setdefault(p.number, []).append((p.name, p.data))
    return sorted(groups.items(), key=lambda g: _order(g[0])) if len(groups) > 1 else []


def single_number(parts: list[Part]) -> str | None:
    """Номер, на котором сходятся все части с номером (части без номера — приложения — не мешают)."""
    found = {p.number for p in parts if p.number}
    return found.pop() if len(found) == 1 else None
