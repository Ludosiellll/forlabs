"""Запуск приложения: без консоли (pythonw), журнал ошибок, второй экземпляр."""

import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn

ROOT = Path(__file__).parents[2]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_server_starts_without_console(tmp_path):
    """pythonw: sys.stdout и sys.stderr = None — uvicorn не должен падать на настройке логов."""
    out = tmp_path / "ok.txt"
    code = (
        "import sys; sys.stdout = None; sys.stderr = None\n"
        "from forlabs.__main__ import build_server\n"
        f"srv = build_server({free_port()}); srv.config.load()\n"
        f"open(r'{out}', 'w').write('ok')\n"
    )
    env = {"FORLABS_HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"}
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, timeout=60)
    assert out.exists(), "сервер не настроился без консоли"
    assert (tmp_path / "home" / "forlabs.log").exists()  # вывод ушёл в журнал


def test_errors_go_to_log(tmp_home):
    import logging

    from forlabs.core.logs import setup_logging

    log = setup_logging()
    logging.getLogger("forlabs").error("проверка журнала")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "проверка журнала" in log.read_text(encoding="utf-8")


def test_second_instance_finds_the_first(tmp_home):
    from forlabs.__main__ import already_running, build_server

    port = free_port()
    assert already_running(port) is False
    srv = build_server(port)
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    try:
        assert already_running(port) is True
    finally:
        srv.should_exit = True
