"""Надёжная запись файлов: выключение питания или полный диск не оставляют обрезанный файл вместо старого."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("forlabs")


def atomic_write_text(path: Path | str, text: str, encoding: str = "utf-8") -> None:
    """Пишет во временный файл рядом, сбрасывает на диск (fsync) и заменяет целиком. При ошибке старый файл цел."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(4):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # антивирус или индексатор Windows на миг держит файл
                if attempt == 3:
                    raise
                time.sleep(0.15 * (attempt + 1))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def atomic_write_bytes(path: Path | str, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def quarantine(path: Path | str, reason: str = "") -> Path | None:
    """Битый файл переименовывается в *.broken-<время> (для разбора), приложение работает дальше."""
    path = Path(path)
    target = path.with_name(f"{path.name}.broken-{datetime.now():%Y%m%d-%H%M%S}")
    try:
        os.replace(path, target)
    except OSError:
        return None
    log.warning("Файл %s повреждён (%s) — отложен в %s", path, reason, target.name)
    return target
