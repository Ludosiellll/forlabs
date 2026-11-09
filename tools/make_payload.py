"""Сборка установщика: папка ForLabs.dist (Nuitka standalone) → payload.zip для ForLabsSetup.exe.

    python tools/make_payload.py build/run_forlabs.dist build/payload.zip
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def make_payload(dist: Path, out: Path) -> int:
    dist, out = Path(dist), Path(out)
    if not (dist / "ForLabs.exe").is_file():
        raise SystemExit(f"В {dist} нет ForLabs.exe — сначала собери программу")
    files = sorted(p for p in dist.rglob("*") if p.is_file())
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in files:
            z.write(p, p.relative_to(dist).as_posix())
    tmp.replace(out)
    return len(files)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    n = make_payload(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"payload: {n} файлов, {Path(sys.argv[2]).stat().st_size // 2**20} МБ")
