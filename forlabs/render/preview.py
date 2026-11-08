"""Предпросмотр: страницы PDF → PNG; поиск страниц, где начинаются лабы."""

from __future__ import annotations

from pathlib import Path

import pymupdf


def pdf_to_pngs(pdf: Path, outdir: Path, zoom: float = 1.5) -> list[Path]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("page-*.png"):
        old.unlink()
    out = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            path = outdir / f"page-{i:03d}.png"
            pix.save(path)
            out.append(path)
    return out


def page_count(pdf: Path) -> int:
    with pymupdf.open(pdf) as doc:
        return doc.page_count


def heading_pages(pdf: Path, headings: list[str]) -> dict[str, int]:
    """Номер страницы (с 1), где строка заголовка стоит отдельной строкой — не в содержании."""
    found: dict[str, int] = {}
    with pymupdf.open(pdf) as doc:
        toc_page = None
        for i, page in enumerate(doc, start=1):
            lines = [ln.strip() for ln in page.get_text("text").splitlines()]
            if toc_page is None and "Содержание" in lines:
                toc_page = i
                continue
            for h in headings:
                if h not in found and h in lines:
                    found[h] = i
    return found
