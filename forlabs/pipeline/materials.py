"""Файлы к лабе, которые выдал преподаватель (данные, текст для разбора, код-заготовка, таблица).

Модель читает их целиком — в каждом задании и в чате правок. Поэтому объём ограничен: файл перечитывается при
каждом обращении к модели, и справочник на 200 страниц съел бы дневной лимит на одной лабе (и не влез бы в запрос).
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from forlabs.ingest.extract import CONTROL_CHARS, IMAGE_EXT, extract_text, read_text_file

PAGE_CHARS = 2000  # знаков на страницу Word (Times New Roman 14, полуторный интервал)
MATERIALS_MAX = 40_000  # на все файлы лабы: ≈ 20 страниц, ≈ 13 тыс. токенов в каждом обращении к модели
MAX_FILES = 6

# код и данные читаются как есть: без склейки строк и переносов, отступы важны
RAW_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".log", ".sql",
           ".py", ".cs", ".java", ".kt", ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".scss", ".php",
           ".c", ".cpp", ".cc", ".h", ".hpp", ".go", ".rb", ".rs", ".swift", ".pas", ".dpr", ".bas", ".vb",
           ".bat", ".cmd", ".ps1", ".sh", ".asm", ".r", ".m", ".pl", ".lua", ".dart", ".xaml", ".resx"}
ACCEPT = ".pdf,.docx,.xlsx," + ",".join(sorted(RAW_EXT | IMAGE_EXT))


def pages(chars: int) -> int:
    return max(1, -(-chars // PAGE_CHARS))


def read_material(path: Path, tesseract: str | None = None) -> str:
    """Текст файла целиком. Неподдерживаемый формат — ValueError с подсказкой."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in RAW_EXT:
        return CONTROL_CHARS.sub("", read_text_file(path).replace("\r\n", "\n").replace("\r", "\n")).strip()
    if ext == ".xlsx":
        return _xlsx(path)
    if ext in {".pdf", ".docx"} | IMAGE_EXT:
        return extract_text(path, tesseract).text.strip()
    raise ValueError(f"формат {ext or 'без расширения'} не читается: сохрани как DOCX, PDF, XLSX, TXT или CSV")


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "p": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]*", ref or "").group(0):
        n = n * 26 + ord(ch) - 64
    return max(n - 1, 0)


def _xlsx(path: Path) -> str:
    """Таблица Excel → листы, строки через табуляцию. Без openpyxl: книга — это zip с XML."""
    from lxml import etree

    def xml(z: zipfile.ZipFile, name: str):
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
        return etree.fromstring(z.read(name), parser)

    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise ValueError("файл Excel повреждён — открой и пересохрани его как .xlsx") from e
    with z:
        names = set(z.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            shared = ["".join(si.itertext()) for si in xml(z, "xl/sharedStrings.xml").findall("m:si", _NS)]
        targets = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for rel in xml(z, "xl/_rels/workbook.xml.rels").findall("p:Relationship", _NS):
                t = rel.get("Target", "").lstrip("/")
                targets[rel.get("Id")] = t if t.startswith("xl/") else f"xl/{t}"
        out: list[str] = []
        for sheet in xml(z, "xl/workbook.xml").iter(f"{{{_NS['m']}}}sheet"):
            target = targets.get(sheet.get(f"{{{_NS['r']}}}id"))
            if target not in names:
                continue
            rows: list[str] = []
            for row in xml(z, target).iter(f"{{{_NS['m']}}}row"):
                cells: list[str] = []
                for c in row.findall("m:c", _NS):
                    kind, v = c.get("t"), c.find("m:v", _NS)
                    if kind == "inlineStr":
                        val = "".join(c.find("m:is", _NS).itertext()) if c.find("m:is", _NS) is not None else ""
                    elif v is None:
                        continue
                    elif kind == "s":
                        i = int(v.text or 0)
                        val = shared[i] if 0 <= i < len(shared) else ""
                    else:
                        val = v.text or ""
                    j = _col(c.get("r", ""))
                    cells += [""] * (j - len(cells) + 1) if j >= len(cells) else []
                    cells[j] = val
                if any(x.strip() for x in cells):
                    rows.append("\t".join(cells).rstrip())
            if rows:
                out.append(f"Лист «{sheet.get('name', '')}»:\n" + "\n".join(rows))
        return "\n\n".join(out).strip()
