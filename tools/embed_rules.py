"""Сборка exe: правила модели (forlabs/rules/**/*.md) → модуль forlabs/_embedded_rules.py.

Nuitka компилирует модуль в машинный код, а текст правил лежит в нём сжатым и перемешанным —
поиском строк по exe его не найти, и папки rules рядом с программой нет.

    python tools/embed_rules.py            # создать forlabs/_embedded_rules.py
"""

from __future__ import annotations

import json
import os
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TEMPLATE = '''"""Создано tools/embed_rules.py при сборке. Не редактировать."""

import json as _json
import zlib as _zlib

_KEY = {key!r}
_DATA = {data!r}


def load() -> dict:
    raw = bytes(b ^ _KEY[i % len(_KEY)] for i, b in enumerate(_DATA))
    return _json.loads(_zlib.decompress(raw).decode("utf-8"))
'''


def collect(rules_dir: Path) -> dict[str, str]:
    return {p.relative_to(rules_dir).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(rules_dir.rglob("*.md"))}


def write_module(rules_dir: Path, out: Path) -> int:
    files = collect(rules_dir)
    if not files:
        raise SystemExit(f"Нет правил в {rules_dir}")
    packed = zlib.compress(json.dumps(files, ensure_ascii=False).encode("utf-8"), 9)
    key = os.urandom(32)
    data = bytes(b ^ key[i % len(key)] for i, b in enumerate(packed))
    out.write_text(TEMPLATE.format(key=key, data=data), encoding="utf-8")
    return len(files)


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else ROOT / "forlabs" / "_embedded_rules.py"
    n = write_module(ROOT / "forlabs" / "rules", out)
    print(f"Встроено правил: {n} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
