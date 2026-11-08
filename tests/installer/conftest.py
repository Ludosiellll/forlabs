import zipfile
from pathlib import Path

import pytest


class FakeShell:
    """Ярлыки и реестр в памяти — вместо Windows."""

    def __init__(self, programs: Path, desktop: Path):
        self.programs, self.desktop = programs, desktop
        self.shortcuts: dict[Path, dict] = {}
        self.entry: dict | None = None

    def programs_dir(self) -> Path:
        return self.programs

    def desktop_dir(self) -> Path:
        return self.desktop

    def create_shortcut(self, lnk: Path, target: Path, workdir: Path, description: str) -> None:
        self.shortcuts[lnk] = {"target": target, "workdir": workdir, "description": description}

    def remove_shortcut(self, lnk: Path) -> None:
        self.shortcuts.pop(lnk, None)

    def write_uninstall_entry(self, values: dict) -> None:
        self.entry = dict(values)

    def read_uninstall_entry(self) -> dict | None:
        return self.entry

    def remove_uninstall_entry(self) -> None:
        self.entry = None


@pytest.fixture
def shell(tmp_path):
    return FakeShell(tmp_path / "Пуск" / "Программы", tmp_path / "Рабочий стол")


def make_payload(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


@pytest.fixture
def payload(tmp_path):
    return make_payload(tmp_path / "payload.zip", {
        "ForLabs.exe": b"MZ" + b"x" * 5000,
        "python312.dll": b"d" * 20000,
        "forlabs/ui/templates/base.html": "<html>Шаблон</html>".encode(),
        "forlabs/render/assets/base.docx": b"PK" + b"y" * 3000,
        "pymorphy3_dicts_ru/data/words.dawg": b"w" * 9000,
    })
