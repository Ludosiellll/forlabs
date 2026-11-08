"""Аудит-2, запуск и Windows: журнал, Word, один экземпляр, второй пользователь, окно, сертификаты."""

import socket
import ssl
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "render"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# D1: под pythonw вывод не держит forlabs.log вторым дескриптором (иначе Windows не даёт его повернуть)
def test_console_output_goes_through_logging(tmp_home, monkeypatch):
    import logging

    from forlabs.core import logs

    tmp_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(logs, "_configured", None)
    log = logs.setup_logging()
    assert getattr(sys.stdout, "name", "") != str(log) and getattr(sys.stderr, "name", "") != str(log)
    print("строка из print")
    sys.stderr.write("трассировка\n")
    for h in logging.getLogger().handlers:
        h.flush()
    text = log.read_text(encoding="utf-8")
    assert "строка из print" in text and "трассировка" in text
    assert logging.getLogger("httpx").level >= logging.WARNING


# D2: зависший при запуске Word находится из родителя по командной строке /Automation и завершается
def test_automation_word_pids_parsing():
    from forlabs.render.word_com import parse_word_processes

    listing = ('1234 "C:\\Program Files\\Microsoft Office\\root\\Office16\\WINWORD.EXE" /Automation -Embedding\n'
               '5678 "C:\\Program Files\\Microsoft Office\\root\\Office16\\WINWORD.EXE" /n "D:\\курсовая.docx"\n')
    assert parse_word_processes(listing) == {1234}


def test_word_hanging_at_startup_is_killed(tmp_path, monkeypatch, tmp_home):
    import fake_word_child

    from forlabs.render import word_com

    tmp_home.mkdir(parents=True, exist_ok=True)
    seen = iter([set(), {777}, {777}, {777}, {777}, {777}, {777}, {777}, {777}, {777}, {777}])
    monkeypatch.setattr(word_com, "_automation_word_pids", lambda: next(seen, {777}))
    killed = []
    monkeypatch.setattr(word_com, "_kill_pids", lambda pids: killed.extend(pids))
    with pytest.raises(TimeoutError):
        word_com.finalize_isolated(tmp_path / "a.docx", tmp_path / "a.pdf", "x", timeout=2,
                                   target=fake_word_child.silent_child)
    assert 777 in killed


def test_stale_automation_word_killed_at_start(tmp_home, monkeypatch):
    from forlabs.render import word_com

    tmp_home.mkdir(parents=True, exist_ok=True)
    word_com._remember_pids({555, 556})
    monkeypatch.setattr(word_com, "_automation_word_pids", lambda: {555})  # 556 уже нет, а 557 — не наш
    killed = []
    monkeypatch.setattr(word_com, "_kill_pids", lambda pids: killed.extend(pids))
    word_com.cleanup_stale_word()
    assert killed == [555] and word_com._remembered_pids() == set()


# D3: один экземпляр — блокировка, порт в файле, второй запуск выводит окно первого вперёд
def test_instance_lock_is_exclusive(tmp_home):
    from forlabs.core.instance import InstanceLock

    tmp_home.mkdir(parents=True, exist_ok=True)
    a, b = InstanceLock(), InstanceLock()
    assert a.acquire() and not b.acquire()
    a.release()
    assert b.acquire()
    b.release()


def test_second_launch_focuses_first(tmp_home):
    from forlabs.__main__ import build_server, contact_primary
    from forlabs.core.instance import write_instance
    from forlabs.core.settings import Settings
    from forlabs.ui.context import AppContext

    tmp_home.mkdir(parents=True, exist_ok=True)
    ctx = AppContext(settings=Settings(mode="local"), synchronous=True)
    focused = []
    ctx.focus = lambda: focused.append(1)
    port = free_port()
    srv = build_server(port, ctx, token="s3cret-token")
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    try:
        write_instance(port, "s3cret-token")
        assert contact_primary(timeout=5) == port
        assert focused == [1]
    finally:
        srv.should_exit = True


# H3: другой пользователь Windows (или чужая программа) без токена не видит данные
def test_requests_without_token_are_refused(tmp_home):
    from fastapi.testclient import TestClient

    from forlabs.core.settings import Settings
    from forlabs.ui.app import create_app
    from forlabs.ui.context import AppContext

    tmp_home.mkdir(parents=True, exist_ok=True)
    ctx = AppContext(settings=Settings(mode="local"), synchronous=True)
    client = TestClient(create_app(ctx, token="s3cret-token"), base_url="http://127.0.0.1:8765")
    assert client.get("/profiles", follow_redirects=False).status_code == 403
    r = client.get("/?t=s3cret-token", follow_redirects=False)
    assert r.status_code == 303 and "forlabs_token" in r.headers.get("set-cookie", "")
    assert client.get("/profiles").status_code == 200  # дальше по cookie
    assert client.get("/health").status_code == 200


# H7: окно не открылось (нет WebView2, ошибка .NET) — браузер, а не тихий выход
def test_window_failure_falls_back_to_browser(monkeypatch):
    from forlabs import __main__ as m

    opened = []
    monkeypatch.setattr(m, "_open_window", lambda url, ctx=None: (_ for _ in ()).throw(RuntimeError("WebView2")))
    monkeypatch.setattr(m.webbrowser, "open", opened.append)
    assert m.open_ui("http://127.0.0.1:1/") == "browser" and opened


# H8: Windows пишет ошибку сертификата по-русски — узнаём её по типу, а не по тексту
def test_localized_certificate_error_is_recognized():
    from forlabs.core.netcert import is_cert_error

    cause = ssl.SSLCertVerificationError(1, "Цепочка сертификатов обработана, но обработка прервана")
    e = httpx.ConnectError("[SSL] Цепочка сертификатов обработана")
    e.__cause__ = cause
    assert is_cert_error(e)


# H10: вывод LibreOffice в кодировке Windows не роняет вёрстку
@pytest.mark.skipif(sys.platform == "win32", reason="скрипт-заглушка sh")
def test_libreoffice_output_in_cp1251(tmp_path):
    from forlabs.render.libreoffice import to_pdf

    exe = tmp_path / "soffice.sh"
    exe.write_bytes(b"#!/bin/sh\nprintf '\\xcf\\xf0\\xe8\\xe2\\xe5\\xf2' >&2\nexit 1\n")
    exe.chmod(0o755)
    docx = tmp_path / "r.docx"
    docx.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="LibreOffice не смог"):
        to_pdf(docx, tmp_path / "out", str(exe))


# H11, D8: установка и документы
def test_installer_checks_64bit_and_docs():
    root = Path(__file__).parents[2]
    assert "AMD64" in (root / "Установка.bat").read_text(encoding="utf-8")
    assert "5 минут" in (root / "docs" / "windows-checklist.md").read_text(encoding="utf-8")
    assert "продолжится, когда" not in (root / "README.md").read_text(encoding="utf-8")


def test_old_word_copies_are_cleaned(tmp_path, monkeypatch):
    import os

    from forlabs.render import finalize as fin

    old = tmp_path / "word_dead.docx"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    fresh = tmp_path / "word_now.docx"
    fresh.write_bytes(b"x")
    fin._clean_old_copies(tmp_path)
    assert not old.exists() and fresh.exists()
