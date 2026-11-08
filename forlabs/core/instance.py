"""Один экземпляр ForLabs на пользователя Windows: блокировка файла и адрес запущенного сервера (порт и ключ)."""

from __future__ import annotations

import json
import os
import sys

from forlabs.core import paths
from forlabs.core.fs import atomic_write_text


class InstanceLock:
    """Исключительная блокировка файла в папке данных пользователя (снимается сама при завершении процесса)."""

    def __init__(self, name: str = "forlabs.lock"):
        self.path = paths.data_home() / name
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")  # noqa: SIM115 — держится всё время работы
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


def _instance_file():
    return paths.data_home() / "instance.json"


def write_instance(port: int, token: str) -> None:
    atomic_write_text(_instance_file(), json.dumps({"port": port, "token": token, "pid": os.getpid()}))


def read_instance() -> tuple[int, str] | None:
    try:
        d = json.loads(_instance_file().read_text(encoding="utf-8"))
        return int(d["port"]), str(d["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_instance() -> None:
    try:
        _instance_file().unlink()
    except OSError:
        pass
