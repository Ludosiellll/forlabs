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
XLSX_PART_MAX = 25_000_000  # байт одной части книги (лист, строки) в распакованном виде — больше не читается
_DATE_IDS = set(range(14, 18)) | {22} | set(range(27, 32)) | set(range(36, 37)) | set(range(50, 59))
_TIME_IDS = set(range(18, 22)) | {45, 46, 47}


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]*", ref or "").group(0):
        n = n * 26 + ord(ch) - 64
    return max(n - 1, 0)


def _too_big() -> ValueError:
    return ValueError(f"таблица больше {MATERIALS_MAX // PAGE_CHARS} страниц текста — оставь в ней только нужный лист "
                      "и строки")


def _xlsx(path: Path) -> str:
    """Таблица Excel → листы, строки через табуляцию, даты — датами. Без openpyxl: книга — это zip с XML; листы
    читаются потоком и только до лимита, так что огромная таблица не занимает память."""
    from datetime import datetime, timedelta

    from lxml import etree

    m = _NS["m"]
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as e:
        raise ValueError("файл Excel повреждён — открой и пересохрани его как .xlsx") from e
    with z:
        info = {i.filename: i for i in z.infolist()}

        def part(name: str):
            if info[name].file_size > XLSX_PART_MAX:
                raise _too_big()
            return etree.fromstring(z.read(name), parser)

        def stream(name: str, tag: str):
            if info[name].file_size > XLSX_PART_MAX:
                raise _too_big()
            with z.open(name) as f:
                for _, el in etree.iterparse(f, events=("end",), tag=f"{{{m}}}{tag}", resolve_entities=False,
                                             no_network=True, huge_tree=False):
                    yield el
                    el.clear()
                    while el.getprevious() is not None:
                        del el.getparent()[0]

        if "xl/workbook.xml" not in info:
            raise ValueError("это не таблица Excel — пересохрани её как .xlsx")
        shared = ["".join(t.text or "" for t in si.iter(f"{{{m}}}t"))
                  for si in stream("xl/sharedStrings.xml", "si")] if "xl/sharedStrings.xml" in info else []
        kinds: list[str] = []  # стиль ячейки → "date" / "time" / "datetime" / ""
        if "xl/styles.xml" in info:
            styles = part("xl/styles.xml")
            custom = {int(f.get("numFmtId", "0")): f.get("formatCode", "") for f in styles.iter(f"{{{m}}}numFmt")}
            xfs = styles.find(f"{{{m}}}cellXfs")
            for xf in (xfs.findall(f"{{{m}}}xf") if xfs is not None else []):
                fid = int(xf.get("numFmtId", "0") or 0)
                code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", custom.get(fid, "")).lower()
                is_date = fid in _DATE_IDS or bool(re.search(r"[dy]", code))
                is_time = fid in _TIME_IDS or bool(re.search(r"[hs]", code))
                kinds.append("datetime" if is_date and is_time else "date" if is_date else "time" if is_time else "")
        book = part("xl/workbook.xml")
        pr = book.find(f"{{{m}}}workbookPr")
        base = datetime(1904, 1, 1) if pr is not None and pr.get("date1904") in ("1", "true") else datetime(1899, 12, 30)
        targets = {}
        if "xl/_rels/workbook.xml.rels" in info:
            for rel in part("xl/_rels/workbook.xml.rels").findall("p:Relationship", _NS):
                t = rel.get("Target", "").lstrip("/")
                targets[rel.get("Id")] = t if t.startswith("xl/") else f"xl/{t}"

        def shown(c, raw: str) -> str:
            s = int(c.get("s", "0") or 0)
            kind = kinds[s] if s < len(kinds) else ""
            if not kind:
                return raw
            try:
                when = base + timedelta(days=float(raw))
            except (ValueError, OverflowError):
                return raw
            return when.strftime({"date": "%d.%m.%Y", "time": "%H:%M", "datetime": "%d.%m.%Y %H:%M"}[kind])

        out: list[str] = []
        size = 0
        for sheet in book.iter(f"{{{m}}}sheet"):
            target = targets.get(sheet.get(f"{{{_NS['r']}}}id"))
            if target not in info:
                continue
            rows: list[str] = []
            for row in stream(target, "row"):
                cells: list[str] = []
                for c in row.iter(f"{{{m}}}c"):
                    kind, v = c.get("t"), c.find(f"{{{m}}}v")
                    if kind == "inlineStr":
                        val = "".join(t.text or "" for t in c.iter(f"{{{m}}}t"))
                    elif v is None:
                        val = None
                    elif kind == "s":
                        i = int(v.text or 0)
                        val = shared[i] if 0 <= i < len(shared) else ""
                    elif kind in ("str", "b", "e"):
                        val = v.text or ""
                    else:
                        val = shown(c, v.text or "")
                    j = _col(c.get("r")) if c.get("r") else len(cells)  # без адреса — следующая по порядку
                    if val is None:
                        if not c.get("r"):
                            cells.append("")
                        continue
                    if j >= len(cells):
                        cells += [""] * (j - len(cells) + 1)
                    cells[j] = val
                if any(x.strip() for x in cells):
                    line = "\t".join(cells).rstrip()
                    rows.append(line)
                    size += len(line) + 1
                    if size > MATERIALS_MAX:
                        raise _too_big()
            if rows:
                out.append(f"Лист «{sheet.get('name', '')}»:\n" + "\n".join(rows))
        return "\n\n".join(out).strip()
