"""Где приложение хранит свои данные (не в папке сдачи и не в репозитории)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_home() -> Path:
    env = os.environ.get("FORLABS_HOME")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ForLabs"
    return Path.home() / ".local" / "share" / "forlabs"


def ensure_dirs() -> Path:
    home = data_home()
    for sub in ("profiles", "labs", "memory", "rules"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    return home
