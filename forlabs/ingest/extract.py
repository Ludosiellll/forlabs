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
class Picture:
    """Картинка из методички. page — целая страница (скан, фото): её текст встаёт в методичку как обычный."""
    data: bytes
    page: bool = False


@dataclass
class SourceText:
    text: str
    pages: int = 1
    warnings: list[str] = field(default_factory=list)
    pictures: list[Picture] = field(default_factory=list)  # по порядку меток «[[картинка N]]» в тексте


PICTURE_RE = re.compile(r"\[\[картинка (\d+)\]\]")
MIN_PICTURE_PX = (100, 40)  # меньше — значок, маркер списка или линия, а не содержание
MIN_PICTURE_PT = (50.0, 25.0)  # то же для PDF: размер на странице в пунктах
PICTURE_DPI = 150
PAGE_DPI = 130  # A4 целиком: около 1075×1520 точек — текст читается, картинка не раздувается


def picture_mark(n: int) -> str:
    return f"[[картинка {n}]]"


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
    # метка картинки у края страницы не закрывает собой номер страницы и колонтитул
    lead, trail = [], []
    for lines in split:
        a = b = 0
        while a < len(lines) and PICTURE_RE.fullmatch(lines[a]):
            a += 1
        while b < len(lines) - a and PICTURE_RE.fullmatch(lines[len(lines) - 1 - b]):
            b += 1
        lead.append(lines[:a])
        trail.append(lines[len(lines) - b:] if b else [])
        lines[:] = lines[a:len(lines) - b]

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
    for i, lines in enumerate(split):
        changed = True
        while lines and changed:
            changed = False
            if lines and (_PAGE_NUMBER.fullmatch(lines[0]) or lines[0] in heads):
                lines, changed = lines[1:], True
            if lines and (_PAGE_NUMBER.fullmatch(lines[-1]) or lines[-1] in foots):
                lines, changed = lines[:-1], True
        out.append("\n".join([*lead[i], *lines, *trail[i]]))
    return out


def _page_with_pictures(page, shown: list, start: int) -> tuple[str, list[Picture]]:
    """Текст страницы с метками картинок там, где они стоят (по высоте), и сами картинки, вырезанные со страницы."""
    import pymupdf

    blocks = [b for b in page.get_text("blocks") if b[6] == 0]
    # блоки в файле идут не всегда сверху вниз (номер страницы бывает вторым): картинка встаёт после ближайшего
    # блока над ней, а не «перед первым блоком ниже»
    after: dict[int, list] = {}
    for rect in sorted(shown, key=lambda r: (r.y0, r.x0)):
        above = [(blk[3], i) for i, blk in enumerate(blocks) if blk[3] <= rect.y0 + rect.height / 2]
        after.setdefault(max(above)[1] if above else -1, []).append(rect)
    out, pics = [], []

    def put(rects) -> None:
        for rect in rects:
            pix = page.get_pixmap(clip=rect, dpi=PICTURE_DPI, colorspace=pymupdf.csRGB)
            pics.append(Picture(pix.tobytes("png")))
            out.append(picture_mark(start + len(pics)) + "\n")

    put(after.get(-1, []))
    for i, blk in enumerate(blocks):
        out.append(blk[4])
        put(after.get(i, []))
    return "".join(out), pics


def _pdf(path: Path, pictures: bool = False) -> SourceText:
    import pymupdf

    parts, warnings, found = [], [], []
    with pymupdf.open(path) as doc:
        pages = doc.page_count
        empty = 0
        placed = [page.get_image_info(xrefs=True) for page in doc] if pictures else []
        on_pages: dict[int, int] = {}
        for infos in placed:
            for xref in {i["xref"] for i in infos if i.get("xref")}:
                on_pages[xref] = on_pages.get(xref, 0) + 1
        # одна и та же картинка на большинстве страниц — эмблема в колонтитуле, а не содержание
        furniture = {x for x, c in on_pages.items() if pages >= 3 and c * 2 > pages}
        for n, page in enumerate(doc):
            t = page.get_text("text")
            blank = len(t.strip()) < 20
            if blank:
                empty += 1
            if pictures and blank and (placed[n] or page.get_drawings()):
                pix = page.get_pixmap(dpi=PAGE_DPI, colorspace=pymupdf.csRGB)
                found.append(Picture(pix.tobytes("png"), page=True))
                t = picture_mark(len(found)) + "\n"
            elif pictures:
                shown = [pymupdf.Rect(i["bbox"]) & page.rect for i in placed[n] if i.get("xref") not in furniture]
                shown = [r for r in shown if r.width >= MIN_PICTURE_PT[0] and r.height >= MIN_PICTURE_PT[1]]
                if shown:
                    t, pics = _page_with_pictures(page, shown, len(found))
                    found.extend(pics)
            parts.append(t)
    parts = _strip_page_furniture(parts)
    unread = empty - sum(1 for p in found if p.page)
    if unread and unread == pages:
        warnings.append("В PDF нет текстового слоя (скан). Нужен Tesseract или вставь текст вручную.")
    elif unread:
        warnings.append(f"{unread} стр. без текста (картинки) — проверь, не там ли таблица вариантов.")
    return SourceText(normalize("\n".join(parts)), pages, warnings, found)


def _picture_size(data: bytes) -> tuple[int, int] | None:
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:  # noqa: BLE001 — EMF/WMF и прочее, чего Pillow не знает: размер неизвестен
        return None


def _docx(path: Path, pictures: bool = False) -> SourceText:
    from docx import Document
    from docx.oxml.ns import qn

    d = Document(str(path))
    found: list[Picture] = []
    embed, legacy = qn("r:embed"), qn("r:id")
    old_picture = "{urn:schemas-microsoft-com:vml}imagedata"  # картинки старого Word (VML)

    def marks(el) -> list[str]:
        """Метки картинок внутри элемента, по порядку; значки и линии пропускаются."""
        if not pictures:
            return []
        out = []
        for node in el.iter(qn("a:blip"), old_picture):
            rid = node.get(embed) or node.get(legacy)
            part = d.part.related_parts.get(rid) if rid else None
            data = getattr(part, "blob", None)
            if not data:
                continue
            size = _picture_size(data)
            if size is not None and (size[0] < MIN_PICTURE_PX[0] or size[1] < MIN_PICTURE_PX[1]):
                continue
            found.append(Picture(data))
            out.append(picture_mark(len(found)))
        return out

    def text_of(el) -> str:
        return "".join(t.text or "" for t in el.iter(qn("w:t")))

    lines: list[str] = []
    for el in d.element.body.iterchildren():
        if el.tag == qn("w:p"):
            text, inside = text_of(el), marks(el)
            if text.strip() or not inside:  # абзац из одной картинки не оставляет пустой строки
                lines.append(text)
            lines.extend(inside)
        elif el.tag == qn("w:tbl"):
            for tr in el.iter(qn("w:tr")):
                cells = [" ".join([text_of(tc).strip(), *marks(tc)]).strip() for tc in tr.iter(qn("w:tc"))]
                lines.append(" | ".join(cells))
    return SourceText(normalize("\n".join(lines)), pictures=found)


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


def _image(path: Path, tesseract: str | None, pictures: bool = False) -> SourceText:
    exe = find_tesseract(tesseract)
    if not exe and pictures:  # без Tesseract фото читает модель — как целую страницу методички
        return SourceText(picture_mark(1), pictures=[Picture(Path(path).read_bytes(), page=True)])
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


def extract_text(path: Path, tesseract: str | None = None, pictures: bool = False) -> SourceText:
    """pictures — пометить в тексте места картинок («[[картинка N]]») и вернуть сами картинки: их читает модель.
    Без него текст такой же, как раньше (поиск номера, связанные отчёты, файлы к лабе)."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf(path, pictures)
    if ext == ".docx":
        return _docx(path, pictures)
    if ext in TEXT_EXT:
        return SourceText(normalize(read_text_file(path)))
    if ext in IMAGE_EXT:
        return _image(path, tesseract, pictures)
    raise ValueError(f"Формат {ext or 'без расширения'} не поддерживается: нужен PDF, DOCX, TXT или картинка")


def extract_many(paths: list[Path], tesseract: str | None = None, pictures: bool = False) -> SourceText:
    parts, warnings, pages, found = [], [], 0, []
    for p in paths:
        s = extract_text(p, tesseract, pictures)
        shift = len(found)  # метки второго файла продолжают счёт первого
        parts.append(PICTURE_RE.sub(lambda m: picture_mark(int(m.group(1)) + shift), s.text) if shift else s.text)
        found.extend(s.pictures)
        warnings.extend(s.warnings)
        pages += s.pages
    return SourceText(normalize("\n\n".join(parts)), pages, warnings, found)
