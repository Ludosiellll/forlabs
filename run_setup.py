"""Точка входа установщика ForLabsSetup.exe (Nuitka onefile; payload.zip и загрузчик WebView2 лежат рядом).

    ForLabsSetup.exe                                       окно установки
    ForLabsSetup.exe --silent [--target D] [--no-desktop] [--launch] [--log файл]   без окна
"""

from __future__ import annotations

import sys
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent  # в onefile — временная папка распаковки с данными


def main(argv: list[str] | None = None) -> int:
    from forlabs.installer import app

    argv = sys.argv[1:] if argv is None else argv
    payload = Path(argv[argv.index("--payload") + 1]) if "--payload" in argv else BUNDLE / "payload.zip"
    return app.run(payload, BUNDLE, argv)


if __name__ == "__main__":
    raise SystemExit(main())
