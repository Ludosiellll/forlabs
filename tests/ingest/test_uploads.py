"""Файл из поля «Методичка»: RTF → DOCX, ZIP → документы из него (остальное — файлы к лабе)."""

import io
import zipfile

from forlabs.ingest.uploads import methodology_files


def _rtf(text: str) -> bytes:
    body = "".join(f"\\'{b:02x}" for b in text.encode("cp1251"))
    return ("{\\rtf1\\ansi\\ansicpg1251 " + body + "\\par}").encode("ascii")


def test_rtf_becomes_docx(tmp_path):
    p = tmp_path / "ЛР4.rtf"
    p.write_bytes(_rtf("Лабораторная работа №4"))
    docs, archive = methodology_files(p.name, p, tmp_path / "work")
    assert [n for n, _ in docs] == ["ЛР4.docx"] and archive is None
    assert docs[0][1][:2] == b"PK"


def test_zip_gives_documents_and_keeps_the_rest_for_lab_files(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ЛР4/методичка.rtf", _rtf("Лабораторная работа №4"))
        z.writestr("ЛР4/задание.pdf", b"%PDF-1.4 stub")
        z.writestr("ЛР4/sprites/key.png", b"png")
    p = tmp_path / "лаба.zip"
    p.write_bytes(buf.getvalue())
    docs, archive = methodology_files(p.name, p, tmp_path / "work")
    assert sorted(n for n, _ in docs) == ["задание.pdf", "методичка.docx"]
    assert archive == p  # в нём есть картинка — его разбирают как файлы к лабе


def test_zip_with_documents_only_needs_no_lab_files(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("методичка.txt", "Лабораторная работа №4".encode())
    p = tmp_path / "m.zip"
    p.write_bytes(buf.getvalue())
    docs, archive = methodology_files(p.name, p, tmp_path / "work")
    assert [n for n, _ in docs] == ["методичка.txt"] and archive is None


def test_plain_files_pass_through(tmp_path):
    p = tmp_path / "лаба.pdf"
    p.write_bytes(b"%PDF-1.4 stub")
    assert methodology_files(p.name, p, tmp_path / "w") == ([("лаба.pdf", b"%PDF-1.4 stub")], None)
