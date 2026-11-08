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


def log_path() -> Path:
    return paths.data_home() / "forlabs.log"


def setup_logging() -> Path:
    global _configured
    f = log_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        stream = open(f, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 — живёт всё время работы
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
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

    def log_thread_error(args):  # ошибки фоновых потоков — в журнал, а не в никуда
        logging.getLogger("forlabs").error("Ошибка в потоке %s", args.thread.name if args.thread else "?",
                                           exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = log_thread_error
    _configured = f
    return f
