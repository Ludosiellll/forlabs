"""ZIP от преподавателя: безопасная распаковка в обычную папку."""

import io
import zipfile

import pytest

from forlabs.ingest.archive import unpack


def _zip(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return path


def test_files_and_folders_are_unpacked(tmp_path):
    z = _zip(tmp_path / "a.zip", [("point-click/key.png", b"png"), ("readme.txt", "привет".encode()),
                                  ("__MACOSX/._key.png", b"x"), ("point-click/.DS_Store", b"x"),
                                  ("Thumbs.db", b"x")])
    files = unpack(z, tmp_path / "out")
    rel = sorted(p.relative_to(tmp_path / "out").as_posix() for p in files)
    assert rel == ["point-click/key.png", "readme.txt"]
    assert (tmp_path / "out" / "readme.txt").read_text(encoding="utf-8") == "привет"


def test_paths_outside_the_folder_are_refused(tmp_path):
    z = _zip(tmp_path / "evil.zip", [("../../escape.txt", b"x"), ("ok.txt", b"y")])
    with pytest.raises(ValueError, match="вне папки"):
        unpack(z, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_russian_names_from_windows_archives(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("QQQQQQQQQ.docx", b"doc")
    # как пишет архиватор Windows: имя в cp866, без флага UTF-8 (длина та же — заголовки не сдвигаются)
    (tmp_path / "w.zip").write_bytes(buf.getvalue().replace(b"QQQQQQQQQ.docx", "Методичка.docx".encode("cp866")))
    files = unpack(tmp_path / "w.zip", tmp_path / "out")
    assert [p.name for p in files] == ["Методичка.docx"]


def test_zip_bomb_is_refused(tmp_path):
    z = _zip(tmp_path / "bomb.zip", [("big.bin", b"\0" * (60 * 1024 * 1024))])
    with pytest.raises(ValueError, match="сжат"):
        unpack(z, tmp_path / "out")


def test_not_a_zip(tmp_path):
    (tmp_path / "x.zip").write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="не ZIP"):
        unpack(tmp_path / "x.zip", tmp_path / "out")
