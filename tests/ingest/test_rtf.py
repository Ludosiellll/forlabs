"""Методичка в RTF: текст (1251 и Unicode) и картинки (PNG, WMF из полос, как сохраняет WordPad) → DOCX."""

import io
import struct

from PIL import Image

from forlabs.ingest.extract import extract_text
from forlabs.ingest.rtf import rtf_to_docx


def _png(color, size=(120, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _dib(color, w, h) -> bytes:
    """BITMAPINFOHEADER + 24-битные пиксели снизу вверх, строки выровнены по 4 байта."""
    row = (bytes(color[::-1]) * w).ljust((w * 3 + 3) // 4 * 4, b"\0")
    header = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(row) * h, 0, 0, 0, 0)
    return header + row * h


def _stretchdib(dib, x, y, w, h) -> bytes:
    params = struct.pack("<IHhhhhhhhh", 0x00CC0020, 0, h, w, 0, 0, h, w, y, x) + dib
    if len(params) % 2:
        params += b"\0"
    size_words = (6 + len(params)) // 2
    return struct.pack("<IH", size_words, 0x0F43) + params


def _wmf_two_bands(w=100, band=40) -> bytes:
    """Картинка 100×80 из двух полос: верх красный, низ синий (так WordPad режет большие картинки)."""
    records = _stretchdib(_dib((255, 0, 0), w, band), 0, 0, w, band) + \
        _stretchdib(_dib((0, 0, 255), w, band), 0, band, w, band) + struct.pack("<IH", 3, 0)
    header = struct.pack("<HHHIHIH", 1, 9, 0x0300, (18 + len(records)) // 2, 0, 0, 0)
    return header + records


def _hex(data: bytes, width=64) -> str:
    h = data.hex()
    return "\r\n".join(h[i:i + width] for i in range(0, len(h), width))


def _rtf(tmp_path):
    text = ("{\\rtf1\\ansi\\ansicpg1251\\deff0{\\fonttbl{\\f0\\fnil\\fcharset204 Times New Roman;}}"
            "{\\*\\generator Riched20 10.0.19041}\\viewkind4\\uc1\\pard\\f0\\fs28 "
            "\\'cb\\'e0\\'e1\\'ee\\'f0\\'e0\\'f2\\'ee\\'f0\\'ed\\'e0\\'ff \\'f0\\'e0\\'e1\\'ee\\'f2\\'e0 \\'b94\\par\n"
            "\\u1057?\\u1094?\\u1077?\\u1085?\\u1072? Unity\\tab ok\\par\n"
            "{\\pict{\\*\\picprop}\\pngblip\\picw120\\pich60 " + _hex(_png((0, 160, 0))) + "}\\par\n"
            "{\\pict\\wmetafile8\\picw100\\pich80 " + _hex(_wmf_two_bands()) + "}\\par\n"
            "\\'ca\\'ee\\'ed\\'e5\\'f6\\par}")
    p = tmp_path / "lab.rtf"
    p.write_bytes(text.encode("ascii"))
    return p


def test_text_and_pictures_come_through(tmp_path):
    out = rtf_to_docx(_rtf(tmp_path), tmp_path / "lab.docx")
    assert out.pictures == 2 and out.skipped == 0
    st = extract_text(tmp_path / "lab.docx", pictures=True)
    assert "Лабораторная работа №4" in st.text and "Сцена Unity" in st.text and "Конец" in st.text
    assert len(st.pictures) == 2
    green = Image.open(io.BytesIO(st.pictures[0].data)).convert("RGB")
    assert green.getpixel((5, 5)) == (0, 160, 0)


def test_wmf_bands_are_put_together(tmp_path):
    rtf_to_docx(_rtf(tmp_path), tmp_path / "lab.docx")
    st = extract_text(tmp_path / "lab.docx", pictures=True)
    img = Image.open(io.BytesIO(st.pictures[1].data)).convert("RGB")
    w, h = img.size
    assert abs(w / h - 100 / 80) < 0.05
    top, bottom = img.getpixel((w // 2, h // 4)), img.getpixel((w // 2, 3 * h // 4))
    assert top[0] > 200 and top[2] < 60 and bottom[2] > 200 and bottom[0] < 60


def test_rtf_methodology_is_read_directly(tmp_path):
    st = extract_text(_rtf(tmp_path), pictures=True)
    assert "Лабораторная работа №4" in st.text and len(st.pictures) == 2


def test_unknown_picture_format_is_skipped_not_fatal(tmp_path):
    p = tmp_path / "x.rtf"
    p.write_bytes(b"{\\rtf1\\ansi\\ansicpg1251 \\'d2\\'e5\\'ea\\'f1\\'f2\\par{\\pict\\macpict " + b"00" * 50 + b"}\\par}")
    out = rtf_to_docx(p, tmp_path / "x.docx")
    assert out.pictures == 0 and out.skipped == 1
    assert "Текст" in extract_text(tmp_path / "x.docx").text


def test_control_characters_do_not_break_the_document(tmp_path):
    p = tmp_path / "c.rtf"
    p.write_bytes(b"{\\rtf1\\ansi\\ansicpg1251 \\'d2\\'e5\\'ea\\'f1\\'f2\\'00\\'01 \\u7?\\par}")
    rtf_to_docx(p, tmp_path / "c.docx")
    assert extract_text(tmp_path / "c.docx").text.startswith("Текст")
