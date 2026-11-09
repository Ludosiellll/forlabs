"""Свой MySQL для снимков: сервер студента (или установленный ForLabs) с отдельной временной папкой данных на
свободном порту 127.0.0.1. Базы студента не трогаются, а каждая лаба начинается с чистого сервера (CREATE DATABASE
не упирается в базу прошлой лабы)."""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

EXE = ".exe" if sys.platform == "win32" else ""


class MysqlError(Exception):
    pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class LabServer:
    proc: subprocess.Popen
    port: int
    bindir: Path
    data: Path

    def client(self) -> list[str]:
        """Консоль mysql, как «MySQL Command Line Client», но к своему серверу."""
        return [str(self.bindir / f"mysql{EXE}"), "--no-defaults", "-u", "root", "-h", "127.0.0.1",
                f"--port={self.port}", "--default-character-set=utf8mb4"]

    def stop(self) -> None:
        try:
            subprocess.run([str(self.bindir / f"mysqladmin{EXE}"), "--no-defaults", "-u", "root", "-h", "127.0.0.1",
                            f"--port={self.port}", "shutdown"], capture_output=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.proc.wait(20)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.data, ignore_errors=True)


def start(mysql_client: str, work_root: Path, timeout: float = 90) -> LabServer:
    """mysql_client — путь к mysql(.exe) из languages.detect(); mysqld ищется рядом."""
    bindir = Path(mysql_client).parent
    mysqld = bindir / f"mysqld{EXE}"
    if not mysqld.exists():
        raise MysqlError("рядом с mysql нет сервера mysqld — поставь MySQL Server (в «Настройках» → «Программы для "
                         "лаб»)")
    data = Path(work_root) / f"mysql-{uuid.uuid4().hex[:8]}"
    data.parent.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    base = bindir.parent
    init = subprocess.run([str(mysqld), "--no-defaults", f"--basedir={base}", f"--datadir={data}",
                           "--initialize-insecure"], capture_output=True, text=True, timeout=300, creationflags=flags)
    if init.returncode != 0:
        shutil.rmtree(data, ignore_errors=True)
        raise MysqlError(f"сервер MySQL не подготовился: {(init.stderr or init.stdout).strip()[-300:]}")
    port = _free_port()
    proc = subprocess.Popen([str(mysqld), "--no-defaults", f"--basedir={base}", f"--datadir={data}", f"--port={port}",
                             "--bind-address=127.0.0.1", "--mysqlx=OFF", "--skip-log-bin"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    server = LabServer(proc, port, bindir, data)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            shutil.rmtree(data, ignore_errors=True)
            raise MysqlError("сервер MySQL не запустился")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return server
        except OSError:
            time.sleep(0.5)
    server.stop()
    raise MysqlError("сервер MySQL не ответил за минуту")
