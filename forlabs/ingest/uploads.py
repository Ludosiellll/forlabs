"""Файлы из поля «Методичка»: RTF сразу превращается в DOCX (его и хранит лаба), ZIP — в документы из архива.

Остальное из архива (картинки, код, данные) разбирается как файлы к лабе: для этого возвращается путь к архиву.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from forlabs.ingest.archive import unpack

DOC_EXT = {".pdf", ".docx", ".rtf", ".txt", ".md"}


def _doc(path: Path, work: Path, name: str | None = None) -> tuple[str, bytes]:
    name = Path(name or path.name).name
    if Path(name).suffix.lower() == ".rtf":
        from forlabs.ingest.rtf import rtf_to_docx

        out = work / (Path(name).stem + ".docx")
        rtf_to_docx(path, out)
        return out.name, out.read_bytes()
    return name, path.read_bytes()


def methodology_files(name: str, path: Path, work: Path) -> tuple[list[tuple[str, bytes]], Path | None]:
    """→ ([(имя, байты)] для разбора методички, путь к архиву, если в нём есть что-то кроме документов).
    ValueError — архив не распаковывается (причина по-русски)."""
    path, work = Path(path), Path(work)
    ext = Path(name).suffix.lower()
    work.mkdir(parents=True, exist_ok=True)
    if ext != ".zip":
        return [_doc(path, work, name)], None
    folder = work / "zip"
    shutil.rmtree(folder, ignore_errors=True)
    files = unpack(path, folder)
    docs = [_doc(f, work) for f in files if f.suffix.lower() in DOC_EXT]
    rest = any(f.suffix.lower() not in DOC_EXT for f in files)
    shutil.rmtree(folder, ignore_errors=True)
    return docs, (path if rest else None)
