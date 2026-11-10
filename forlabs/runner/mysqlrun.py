"""Свой MySQL/MariaDB для снимков: сервер студента (или установленный ForLabs) с отдельной временной папкой данных
на свободном порту 127.0.0.1. Базы студента не трогаются (запросы модели не попадут на его сервер), каждая лаба
начинается с чистого сервера. В Windows сервер работает с пониженными правами в задании (job): писать он может только
в свою папку, а запросы вида INTO OUTFILE ограничены папкой files внутри неё (secure_file_priv)."""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from forlabs.core.fs import remove_tree

WINDOWS = sys.platform == "win32"
EXE = ".exe" if WINDOWS else ""
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
log = logging.getLogger(__name__)


SYSTEM_DBS = {"information_schema", "mysql", "performance_schema", "sys", "test"}
SYSTEM_USERS = ("root", "mysql.sys", "mysql.session", "mysql.infoschema", "mariadb.sys", "mysql", "pma", "")


class MysqlError(Exception):
    """Сообщение — студенту; detail — для журнала и проверки в CI (код выхода, хвост журнала сервера)."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


def _tail(path: Path, n: int = 1200) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-n:]
    except OSError:
        return ""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def short_path(path: Path) -> Path:
    """Путь без кириллицы (имя пользователя «Иван»): MySQL в Windows понимает такие пути в параметрах не везде.
    Короткое имя 8.3, если оно есть; иначе путь как есть."""
    if not WINDOWS or str(path).isascii():
        return path
    import ctypes

    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
    return Path(buf.value) if 0 < n < len(buf) and buf.value.isascii() else path


def find_server(client: str) -> tuple[Path, Path | None, bool]:
    """(mysqld, программа подготовки папки данных MariaDB для Windows, это MariaDB?) рядом с клиентом mysql."""
    bindir = Path(client).parent
    cands = [bindir / f"mysqld{EXE}", bindir / f"mariadbd{EXE}"]
    if not WINDOWS:
        cands += [Path("/usr/sbin/mysqld"), Path("/usr/sbin/mariadbd")]
    mysqld = next((c for c in cands if c.exists()), None)
    if mysqld is None:
        raise MysqlError("рядом с mysql нет сервера (mysqld): поставь MySQL Server в «Настройках» → «Программы для лаб»")
    try:
        ver = subprocess.run([str(mysqld), "--version"], capture_output=True, text=True, timeout=30,
                             creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.TimeoutExpired):
        ver = ""
    maria = "mariadb" in ver.lower() or mysqld.name.startswith("mariadbd")
    # mysql_install_db.exe у MariaDB для Windows (и в XAMPP) — программа, а не скрипт; в Linux скрипт зависит от
    # окружения, поэтому там папку данных готовим сами (_bootstrap), как это делает сам скрипт
    install = None
    if maria and WINDOWS:
        install = next((c for c in [mysqld.parent / "mariadb-install-db.exe", mysqld.parent / "mysql_install_db.exe"]
                        if c.exists()), None)
    return mysqld, install, maria


BOOTSTRAP_SQL = ["mysql_system_tables.sql", "mysql_performance_tables.sql", "mysql_system_tables_data.sql",
                 "maria_add_gis_sp_bootstrap.sql", "mysql_sys_schema.sql"]


def _share_dir(base: Path) -> Path | None:
    for d in [base / "share" / "mysql", base / "share" / "mariadb", base / "share",
              Path("/usr/share/mysql"), Path("/usr/share/mariadb")]:
        if (d / BOOTSTRAP_SQL[0]).is_file():
            return d
    return None


def _bootstrap(mysqld: Path, base: Path, db: Path, extra: list[str]) -> bool:
    """Системные таблицы MariaDB через mysqld --bootstrap (так же делает mariadb-install-db). Только файлы самого
    сервера, без кода лабы; root@localhost без пароля."""
    share = _share_dir(base)
    if share is None:
        return False
    sql = ["create database if not exists mysql;", "use mysql;", "SET @auth_root_socket=NULL;"]
    sql += [(share / f).read_text(encoding="utf-8") for f in BOOTSTRAP_SQL if (share / f).is_file()]
    db.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run([str(mysqld), "--no-defaults", "--bootstrap", f"--basedir={base}", f"--datadir={db}",
                            "--log-warnings=0", "--enforce-storage-engine=", "--max_allowed_packet=8M",
                            "--net_buffer_length=16K", *extra],
                           input="\n".join(sql).encode("utf-8"), stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=300, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and any((db / "mysql" / f).exists() for f in ("global_priv.frm", "user.frm"))


class _Proc:
    """Процесс сервера: в Windows — с пониженными правами в задании, иначе — обычный."""

    def __init__(self, args: list[str], cwd: Path, low: bool):
        self.launched = None
        self.popen = None
        if WINDOWS and low:
            from forlabs.runner import winapi

            self.launched = winapi.spawn(subprocess.list2cmdline(args), str(cwd), low=True, hidden=True,
                                         memory_mb=2048, max_processes=4)
        else:
            self.popen = subprocess.Popen(args, cwd=str(cwd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          creationflags=NO_WINDOW)

    def wait(self, timeout: float) -> int | None:
        if self.launched is not None:
            return self.launched.wait(timeout)
        try:
            return self.popen.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def alive(self) -> bool:
        return self.launched.alive() if self.launched is not None else self.popen.poll() is None

    def kill(self) -> None:
        if self.launched is not None:
            self.launched.close()
        elif self.popen.poll() is None:
            self.popen.kill()
            self.popen.wait(10)


@dataclass
class LabServer:
    proc: _Proc
    port: int
    client_path: Path
    data: Path
    root: Path | None = None  # своя папка запуска (этап «Проект»): удаляется вместе с данными

    def client(self) -> list[str]:
        """Консоль mysql, как «MySQL Command Line Client», но к своему серверу."""
        return [str(self.client_path), "--no-defaults", "-u", "root", "-h", "127.0.0.1", f"--port={self.port}",
                "--default-character-set=utf8mb4"]

    def query(self, sql: str, timeout: float = 60) -> tuple[int, str]:
        """Один запрос без оформления. → (код выхода, вывод вместе с ошибкой)."""
        r = subprocess.run([*self.client(), "-N", "-e", sql], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, creationflags=NO_WINDOW)
        return r.returncode, (r.stdout + r.stderr).strip()

    def reset(self) -> None:
        """Сервер снова чистый: без баз и пользователей лабы. Перед проверкой скриптов и перед каждым снимком —
        скриптам не нужен DROP DATABASE, а снимки не зависят друг от друга."""
        code, out = self.query("SHOW DATABASES")
        names = [x.strip() for x in out.splitlines()] if code == 0 else []
        drops = [f"DROP DATABASE IF EXISTS `{n.replace('`', '``')}`" for n in names
                 if n and n.lower() not in SYSTEM_DBS]
        code, out = self.query("SELECT CONCAT(QUOTE(user), '@', QUOTE(host)) FROM mysql.user")
        users = [x.strip() for x in out.splitlines()] if code == 0 else []
        drops += [f"DROP USER IF EXISTS {u}" for u in users
                  if u and not any(u.lower().startswith(f"'{k}'@") for k in SYSTEM_USERS)]
        if drops:
            self.query("; ".join(drops))

    def run_file(self, path: Path, timeout: float = 120) -> str:
        """Выполняет скрипт целиком. → "" или первая ошибка: «имя файла: ERROR 1064 (42000) at line 3: …»."""
        try:
            with open(path, "rb") as fh:
                r = subprocess.run([*self.client(), "--batch"], stdin=fh, capture_output=True, timeout=timeout,
                                   creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return f"{Path(path).name}: скрипт выполнялся дольше {int(timeout)} с и был остановлен"
        if r.returncode == 0:
            return ""
        err = r.stderr.decode("utf-8", errors="replace").strip()
        lines = [ln for ln in err.splitlines() if ln.strip() and "Using a password" not in ln]
        return f"{Path(path).name}: {(lines[0] if lines else 'ошибка без текста')[:500]}"

    def stop(self) -> None:
        self.proc.kill()
        remove_tree(self.data, tries=10)  # файлы сервера освобождаются не мгновенно; ключи — «только для чтения»
        if self.root is not None:
            remove_tree(self.root, tries=3)


def start(mysql_client: str, work_root: Path, timeout: float = 90, low: bool = True) -> LabServer:
    """mysql_client — путь к mysql(.exe) из languages.detect(); сервер ищется рядом. work_root — папка, куда сервер
    может писать (в Windows — в LocalLow, с меткой Low)."""
    mysqld, install, maria = find_server(mysql_client)
    data = Path(work_root) / f"mysql-{uuid.uuid4().hex[:8]}"
    (data / "files").mkdir(parents=True, exist_ok=True)
    (data / "tmp").mkdir(exist_ok=True)  # TEMP пользователя серверу с пониженными правами закрыт
    data = short_path(data)
    files, tmp, db = data / "files", data / "tmp", data / "db"
    base = mysqld.parent.parent
    as_root = ["--user=root"] if not WINDOWS and hasattr(os, "geteuid") and os.geteuid() == 0 else []
    code = None
    if maria:
        ok = False
        if install is not None:  # Windows: mysql_install_db.exe знает, где у этой сборки лежат системные таблицы
            p = _Proc([str(install), f"--datadir={db}"], data, low)
            code = p.wait(300)
            p.kill()  # и задание (job) закрыть: зависшая подготовка не остаётся висеть
            ok = code == 0 and (db / "mysql").is_dir()
        if not ok:
            remove_tree(db, tries=3)
            ok = _bootstrap(mysqld, base, db, as_root)
    else:
        p = _Proc([str(mysqld), "--no-defaults", f"--basedir={base}", f"--datadir={db}", "--initialize-insecure",
                   f"--tmpdir={tmp}", f"--log-error={data / 'init.err'}", *as_root], data, low)
        code = p.wait(300)
        p.kill()
        ok = code == 0 and db.is_dir()
    if not ok:
        detail = f"код {code}; {_tail(data / 'init.err')}"
        log.warning("mysqld: папка данных не подготовлена: %s", detail)
        remove_tree(data, tries=3)
        raise MysqlError("сервер MySQL не подготовил папку данных", detail)
    port = _free_port()
    args = [str(mysqld), "--no-defaults", f"--basedir={base}", f"--datadir={db}",
            f"--port={port}", "--bind-address=127.0.0.1", f"--secure-file-priv={files}", f"--tmpdir={tmp}",
            "--character-set-server=utf8mb4",  # у MariaDB 10.4 (XAMPP) по умолчанию latin1: кириллица не вставится
            f"--pid-file={data / 'server.pid'}", f"--log-error={data / 'server.err'}", *as_root]
    if not WINDOWS:
        args.append(f"--socket={data / 'server.sock'}")
    if not maria:
        args += ["--mysqlx=OFF", "--skip-log-bin"]
    server = LabServer(_Proc(args, data, low), port, Path(mysql_client), data)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not server.proc.alive():
            detail = _tail(data / "server.err")
            log.warning("mysqld не запустился: %s", detail)
            server.stop()
            raise MysqlError("сервер MySQL не запустился", detail)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return server
        except OSError:
            time.sleep(0.5)
    server.stop()
    raise MysqlError("сервер MySQL не ответил за полторы минуты")


def check_scripts(server: LabServer, folder: Path, names: list[str]) -> str:
    """Скрипты лабы по порядку на чистом сервере. → "" или первая ошибка (следующие скрипты после неё не идут:
    их ошибки были бы следствием первой). После проверки сервер снова чистый."""
    server.reset()
    try:
        for name in names:
            err = server.run_file(Path(folder).joinpath(*name.split("/")))
            if err:
                return err
        return ""
    finally:
        server.reset()
