"""Журнал приложения: forlabs.log в папке данных (с ротацией). Под pythonw (без консоли) sys.stdout и
sys.stderr равны None — без этого uvicorn падает при старте, а ошибки пропадают бесследно."""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from forlabs.core import paths

_configured: Path | None = None


class _LogWriter:
    """Файлоподобный вывод (print, трассировки) построчно в журнал."""

    encoding = "utf-8"

    def __init__(self, logger: logging.Logger, level: int):
        self.logger, self.level, self._buf = logger, level, ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self.logger.log(self.level, line.rstrip())
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            self.logger.log(self.level, self._buf.rstrip())
        self._buf = ""

    def isatty(self) -> bool:
        return False


def log_path() -> Path:
    return paths.data_home() / "forlabs.log"


def setup_logging() -> Path:
    global _configured
    f = log_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        # вывод — через журнал, а не вторым дескриптором forlabs.log: Windows не даёт повернуть открытый файл
        sys.stdout = sys.stdout or _LogWriter(logging.getLogger("forlabs.console"), logging.INFO)
        sys.stderr = sys.stderr or _LogWriter(logging.getLogger("forlabs.console"), logging.ERROR)
    if _configured == f:
        return f
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_forlabs", False):
            root.removeHandler(h)
    handler = RotatingFileHandler(f, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler._forlabs = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore"):  # каждый запрос — не повод для строки журнала
        logging.getLogger(noisy).setLevel(logging.WARNING)

    def log_thread_error(args):  # ошибки фоновых потоков — в журнал, а не в никуда
        logging.getLogger("forlabs").error("Ошибка в потоке %s", args.thread.name if args.thread else "?",
                                           exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = log_thread_error
    _configured = f
    return f
