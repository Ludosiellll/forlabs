"""Надёжная запись файлов: выключение питания или полный диск не оставляют обрезанный файл вместо старого."""

from __future__ import annotations

import errno
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


def os_error_text(e: OSError) -> str:
    """Ошибка Windows/диска по-русски, с именем файла, без английского текста системы."""
    name = Path(str(e.filename)).name if getattr(e, "filename", None) else ""
    where = f" «{name}»" if name else ""
    if isinstance(e, ConnectionError):
        return "Связь оборвалась — проверь интернет и повтори."
    if e.errno == errno.ENOSPC:
        return "На диске закончилось место — освободи немного места и повтори."
    if isinstance(e, PermissionError) or e.errno in (errno.EACCES, errno.EPERM):
        return (f"Нет доступа к файлу{where}: возможно, он открыт в Word или другой программе, или антивирус его "
                "проверяет. Закрой файл и повтори.")
    if isinstance(e, FileNotFoundError):
        return f"Файл{where} не найден — возможно, его переместили или удалили."
    if e.errno == errno.EROFS:
        return "Диск открыт только для чтения — выбери другую папку."
    return f"Не получилось работать с файлом{where} ({e.strerror or type(e).__name__}). Повтори ещё раз."
