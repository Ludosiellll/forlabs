"""Методичка в RTF → DOCX, который дальше читается как обычно (текст, картинки по местам).

WordPad и старые редакторы хранят картинки в RTF шестнадцатеричным текстом без сжатия (часто WMF из множества
полос), поэтому файл на сотни мегабайт — обычное дело. Разбор идёт потоком по файлу (mmap): текст собирается по
абзацам, картинки декодируются по частям, WMF/EMF собираются из полос в одну картинку и сжимаются в PNG.
Неизвестный формат картинки пропускается и считается в skipped — текст важнее.
"""

from __future__ import annotations

import binascii
import io
import mmap
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

MAX_SIDE = 1600  # как prepare_image: больше модели не нужно, а DOCX остаётся небольшим
CHUNK = 16 * 1024 * 1024
TOKEN = re.compile(rb"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|([^\\{}]+)")
SKIP_DEST = {b"fonttbl", b"colortbl", b"stylesheet", b"info", b"listtable", b"listoverridetable", b"rsidtbl",
             b"generator", b"xmlnstbl", b"mmathPr", b"themedata", b"colorschememapping", b"latentstyles",
             b"datastore", b"pgdsctbl", b"nonshppict", b"header", b"headerl", b"headerr", b"headerf", b"footer",
             b"footerl", b"footerr", b"footerf", b"fldinst", b"object", b"objdata", b"bkmkstart", b"bkmkend"}
PICT_KINDS = {b"pngblip": "png", b"jpegblip": "jpeg", b"emfblip": "emf", b"wmetafile": "wmf", b"dibitmap": "dib",
              b"wbitmap": "ddb", b"macpict": "pict", b"pmmetafile": "pm"}
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")  # в DOCX недопустимы
SYMBOLS = {b"emdash": "—", b"endash": "–", b"bullet": "•", b"lquote": "‘", b"rquote": "’", b"ldblquote": "“",
           b"rdblquote": "”", b"tab": "\t", b"cell": "\t"}


@dataclass
class RtfResult:
    paragraphs: int
    pictures: int
    skipped: int


class _Group:
    __slots__ = ("skip", "uc", "pict")

    def __init__(self, skip=False, uc=1, pict=None):
        self.skip, self.uc, self.pict = skip, uc, pict


def _unhex(m, spans) -> bytes:
    out, carry = bytearray(), b""
    for s, e in spans:
        for i in range(s, e, CHUNK):
            chunk = carry + bytes(m[i:min(i + CHUNK, e)]).translate(None, b" \r\n\t")
            if len(chunk) % 2:
                carry, chunk = chunk[-1:], chunk[:-1]
            else:
                carry = b""
            out += binascii.unhexlify(chunk)
    return bytes(out)


def _dib_image(dib: bytes) -> Image.Image:
    """DIB (заголовок BITMAPINFO + пиксели) → картинка: дописывается заголовок файла BMP."""
    hsize = struct.unpack_from("<I", dib, 0)[0]
    if hsize == 12:
        bits = struct.unpack_from("<H", dib, 10)[0]
        colors = (1 << bits if bits <= 8 else 0) * 3
    else:
        bits, comp = struct.unpack_from("<H", dib, 14)[0], struct.unpack_from("<I", dib, 16)[0]
        if comp in (4, 5):  # внутри JPEG или PNG
            return Image.open(io.BytesIO(dib[hsize:]))
        used = struct.unpack_from("<I", dib, 32)[0] if hsize >= 36 else 0
        colors = (used or (1 << bits if bits <= 8 else 0)) * 4
        if comp == 3 and hsize == 40:
            colors += 12  # маски каналов
    bmp = b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, 14 + hsize + colors) + dib
    img = Image.open(io.BytesIO(bmp))
    img.load()
    return img


def _wmf_tiles(data: bytes) -> list[tuple[int, int, int, int, bytes]]:
    pos = 22 if data[:4] == b"\xd7\xcd\xc6\x9a" else 0  # «размещаемый» заголовок
    pos += struct.unpack_from("<H", data, pos + 2)[0] * 2
    tiles = []
    while pos + 6 <= len(data):
        size, fn = struct.unpack_from("<I", data, pos)[0] * 2, struct.unpack_from("<H", data, pos + 4)[0]
        if fn == 0 or size < 6:
            break
        p, end = pos + 6, min(pos + size, len(data))
        try:
            if fn == 0x0F43:  # META_STRETCHDIB
                _, _, _, _, _, _, dh, dw, yd, xd = struct.unpack_from("<IHhhhhhhhh", data, p)
                dib = data[p + 22:end]
            elif fn == 0x0B41:  # META_DIBSTRETCHBLT
                _, _, _, _, _, dh, dw, yd, xd = struct.unpack_from("<Ihhhhhhhh", data, p)
                dib = data[p + 20:end]
            elif fn == 0x0940:  # META_DIBBITBLT
                _, _, _, dh, dw, yd, xd = struct.unpack_from("<Ihhhhhh", data, p)
                dib = data[p + 16:end]
            else:
                dib = b""
        except struct.error:
            break
        if len(dib) > 40 and dw and dh:
            tiles.append((xd, yd, abs(dw), abs(dh), dib))
        pos += size
    return tiles


def _emf_tiles(data: bytes) -> list[tuple[int, int, int, int, bytes]]:
    pos, tiles = 0, []
    while pos + 8 <= len(data):
        kind, size = struct.unpack_from("<II", data, pos)
        if size < 8:
            break
        try:
            if kind == 81:  # EMR_STRETCHDIBITS
                xd, yd = struct.unpack_from("<ii", data, pos + 24)
                off_bmi, cb_bmi, off_bits, cb_bits = struct.unpack_from("<IIII", data, pos + 48)
                cxd, cyd = struct.unpack_from("<ii", data, pos + 72)
            elif kind == 77:  # EMR_STRETCHBLT
                xd, yd, cxd, cyd = struct.unpack_from("<iiii", data, pos + 24)
                off_bmi, cb_bmi, off_bits, cb_bits = struct.unpack_from("<IIII", data, pos + 84)
            else:
                off_bmi = cb_bmi = 0
        except struct.error:
            break
        if cb_bmi and cxd and cyd:
            dib = data[pos + off_bmi:pos + off_bmi + cb_bmi] + data[pos + off_bits:pos + off_bits + cb_bits]
            tiles.append((xd, yd, abs(cxd), abs(cyd), dib))
        if kind == 14:  # EMR_EOF
            break
        pos += size
    return tiles


def _compose(tiles) -> Image.Image | None:
    """Полосы метафайла — в одну картинку: по их местам на листе, с плотностью пикселей самих полос."""
    images = []
    for xd, yd, dw, dh, dib in tiles:
        try:
            images.append((xd, yd, dw, dh, _dib_image(dib).convert("RGB")))
        except Exception:  # noqa: BLE001 — битая полоса не должна ронять всю картинку
            continue
    if not images:
        return None
    x0, y0 = min(t[0] for t in images), min(t[1] for t in images)
    x1, y1 = max(t[0] + t[2] for t in images), max(t[1] + t[3] for t in images)
    ratio = max(t[4].width / t[2] for t in images)  # пикселей на единицу листа
    scale = min(ratio, MAX_SIDE / max(x1 - x0, y1 - y0))
    canvas = Image.new("RGB", (max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale))), "white")
    for xd, yd, dw, dh, img in images:
        size = (max(1, round(dw * scale)), max(1, round(dh * scale)))
        canvas.paste(img.resize(size), (round((xd - x0) * scale), round((yd - y0) * scale)))
    return canvas


def _picture(kind: str | None, data: bytes) -> bytes | None:
    try:
        if kind in ("png", "jpeg"):
            img = Image.open(io.BytesIO(data))
            img.load()
        elif kind == "dib":
            img = _dib_image(data)
        elif kind == "wmf":
            img = _compose(_wmf_tiles(data))
        elif kind == "emf":
            img = _compose(_emf_tiles(data))
        else:
            return None
    except Exception:  # noqa: BLE001
        return None
    if img is None:
        return None
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _blocks(path: Path) -> tuple[list[tuple[str, object]], int]:
    """Абзацы текста и картинки по порядку: [("p", текст) | ("img", png)], число пропущенных картинок."""
    blocks: list[tuple[str, object]] = []
    para: list[str] = []
    skipped = 0
    codepage = "cp1251"

    def end_paragraph():
        text = _BAD_XML.sub("", "".join(para).replace("\xa0", " ")).strip()
        if text:
            blocks.append(("p", text))
        para.clear()

    with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
        state, stack, pending_skip, first = _Group(), [], 0, True
        for tok in TOKEN.finditer(m):
            word, num, hexch, sym, brace, data = tok.groups()
            if brace == b"{":
                stack.append(state)
                state, first = _Group(state.skip, state.uc, None), True
                continue
            if brace == b"}":
                if state.pict is not None and not state.skip:
                    png = _picture(state.pict["kind"], _unhex(m, state.pict["spans"]))
                    if png:
                        end_paragraph()
                        blocks.append(("img", png))
                    else:
                        skipped += 1
                state, first = (stack.pop() if stack else _Group()), False
                continue
            starts_group, first = first, False
            if sym == b"*" and starts_group:
                state.skip = True
                continue
            if state.skip:
                continue
            if word is not None:
                if starts_group and word in SKIP_DEST:
                    state.skip = True
                elif word == b"pict":
                    state.pict = {"kind": None, "spans": []}
                elif state.pict is not None and word in PICT_KINDS:
                    state.pict["kind"] = PICT_KINDS[word]
                elif word == b"ansicpg" and num:
                    codepage = f"cp{int(num)}"
                elif word == b"uc" and num:
                    state.uc = int(num)
                elif word == b"u" and num:
                    n = int(num)
                    para.append(chr(n + 65536 if n < 0 else n))
                    pending_skip = state.uc
                elif word in (b"par", b"line", b"row", b"sect", b"page"):
                    end_paragraph()
                elif word in SYMBOLS:
                    para.append(SYMBOLS[word])
                continue
            if hexch is not None:
                if pending_skip:
                    pending_skip -= 1
                    continue
                if state.pict is None:
                    try:
                        para.append(bytes([int(hexch, 16)]).decode(codepage, errors="replace"))
                    except LookupError:
                        para.append(bytes([int(hexch, 16)]).decode("cp1251", errors="replace"))
                continue
            if sym is not None:
                if sym == b"~":
                    para.append("\xa0")
                elif sym == b"_":
                    para.append("-")
                elif sym in (b"\\", b"{", b"}"):
                    para.append(sym.decode())
                continue
            if data is not None:
                if state.pict is not None:
                    state.pict["spans"].append(tok.span(6))
                    continue
                text = data.replace(b"\r", b"").replace(b"\n", b"")
                if pending_skip and text:
                    cut = min(pending_skip, len(text))
                    text, pending_skip = text[cut:], pending_skip - cut
                if text:
                    para.append(text.decode(codepage, errors="replace") if text.isascii() is False else text.decode())
        end_paragraph()
    return blocks, skipped


def rtf_to_docx(src: Path, dst: Path) -> RtfResult:
    """RTF → DOCX: абзацы текста и картинки по своим местам (PNG не шире 16 см)."""
    from docx import Document
    from docx.shared import Cm

    blocks, skipped = _blocks(Path(src))
    doc = Document()
    pictures = paragraphs = 0
    for kind, value in blocks:
        if kind == "p":
            doc.add_paragraph(value)
            paragraphs += 1
        else:
            img = Image.open(io.BytesIO(value))
            width = Cm(min(16.0, img.width / 96 * 2.54))
            doc.add_paragraph().add_run().add_picture(io.BytesIO(value), width=width)
            pictures += 1
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dst))
    return RtfResult(paragraphs, pictures, skipped)
