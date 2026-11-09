"""Обновление при плохой связи и других сбоях: нет интернета, обрыв посреди загрузки, портал Wi-Fi, битый файл,
нет места на диске, сервер без докачки, двойной клик, перезапуск посреди загрузки."""

import errno
import hashlib
import json
import threading
import time

import httpx
import pytest

from forlabs.core import update as update_mod
from forlabs.core.update import DOWNLOADS, LATEST_JSON, Updater

DATA = b"MZ" + bytes(range(256)) * 400  # ≈100 КБ «установщика»


class Broken(httpx.SyncByteStream):
    """Отдаёт часть тела и обрывается — как пропавший Wi-Fi."""

    def __init__(self, body: bytes, exc: Exception):
        self.body, self.exc = body, exc

    def __iter__(self):
        yield self.body
        raise self.exc


class Server:
    def __init__(self, data=DATA, version="9.9.9", sha=None):
        self.data = data
        self.meta = {"version": version, "size": len(data), "sha256": sha or hashlib.sha256(data).hexdigest(),
                     "url": f"{DOWNLOADS}v{version}/ForLabsSetup.exe"}
        self.offline = False
        self.portal = False
        self.range_ok = True
        self.drops: list[int] = []  # на каких байтах оборвать очередные загрузки
        self.statuses: list[int] = []  # очередные коды ответа на загрузку (503 и т.п.)
        self.timeouts = 0
        self.downloads: list[str | None] = []  # заголовок Range каждой загрузки

    def handler(self, req: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("сеть недоступна", request=req)
        if self.portal:
            return httpx.Response(200, content="<html><body>Войдите в сеть Wi-Fi колледжа</body></html>".encode())
        if str(req.url) == LATEST_JSON:
            return httpx.Response(200, content=json.dumps(self.meta).encode())
        rng = req.headers.get("range")
        self.downloads.append(rng)
        if self.statuses:
            return httpx.Response(self.statuses.pop(0), content=b"busy")
        start, code = 0, 200
        if rng and self.range_ok:
            start, code = int(rng.split("=")[1].rstrip("-")), 206
        body = self.data[start:]
        if self.timeouts:
            self.timeouts -= 1
            return httpx.Response(code, stream=Broken(body[:1000], httpx.ReadTimeout("тишина", request=req)))
        if self.drops:
            cut = self.drops.pop(0)
            return httpx.Response(code, stream=Broken(body[:cut], httpx.ReadError("обрыв", request=req)))
        return httpx.Response(code, content=body)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=True)


def make(server, tmp_path, current="1.0.2"):
    up = Updater(current=current, http=server.client, folder=tmp_path / "updates", enabled=True)
    up.pauses = (0, 0, 0)
    return up


def ready(up):
    return up.status()["state"] == "ready" and up.installer.read_bytes() == DATA


def test_no_internet_at_start_nothing_is_shown(tmp_path):
    srv = Server()
    srv.offline = True
    up = make(srv, tmp_path)
    assert up.check() is None and up.status()["state"] == "idle" and up.status()["error"] == ""


def test_wifi_login_page_instead_of_check_is_ignored(tmp_path):
    srv = Server()
    srv.portal = True
    up = make(srv, tmp_path)
    assert up.check() is None and up.status()["state"] == "idle"


def test_connection_drops_mid_download_and_resumes_from_same_place(tmp_path):
    srv = Server()
    srv.drops = [40_000, 30_000]  # два обрыва подряд
    up = make(srv, tmp_path)
    up.check()
    up.download()
    assert ready(up)
    assert srv.downloads == [None, "bytes=40000-", "bytes=70000-"]  # каждый раз — с того же места, а не сначала


def test_server_without_resume_starts_over_cleanly(tmp_path):
    srv = Server()
    srv.range_ok = False
    srv.drops = [50_000]
    up = make(srv, tmp_path)
    up.check()
    up.download()
    assert ready(up)  # кусок не «приклеился» дважды


def test_stalled_server_is_retried(tmp_path):
    srv = Server()
    srv.timeouts = 1
    up = make(srv, tmp_path)
    up.check()
    up.download()
    assert ready(up)


def test_server_error_is_retried(tmp_path):
    srv = Server()
    srv.statuses = [503, 502]
    up = make(srv, tmp_path)
    up.check()
    up.download()
    assert ready(up)


def test_internet_gone_for_long_then_back_after_restart(tmp_path):
    srv = Server()
    srv.drops = [60_000]
    up = make(srv, tmp_path)
    up.check()
    orig = srv.handler

    def drop_then_offline(req):  # после первого обрыва сети нет совсем
        if srv.downloads:
            srv.offline = True
        return orig(req)

    srv.handler = drop_then_offline
    up.download()
    s = up.status()
    assert s["state"] == "error" and "Проверь интернет" in s["error"] and "с того же места" in s["error"]
    part = next((tmp_path / "updates").glob("*.part"))
    assert part.stat().st_size == 60_000  # скачанное не выброшено
    # ForLabs закрыли и открыли на следующий день: связь есть — докачивается остаток
    srv.handler, srv.offline, srv.downloads = orig, False, []
    again = make(srv, tmp_path)
    again.check()
    again.download()
    assert ready(again) and srv.downloads == ["bytes=60000-"]


def test_retry_button_after_error_continues(tmp_path):
    srv = Server()
    srv.drops = [10_000, 10_000, 10_000, 10_000]  # 4 обрыва — больше, чем повторов
    up = make(srv, tmp_path)
    up.check()
    up.download()
    assert up.status()["state"] == "error"
    assert up.start_download() is True  # «Повторить»
    for _ in range(500):
        if up.status()["state"] != "downloading":
            break
        time.sleep(0.01)
    assert ready(up)


def test_wifi_login_page_instead_of_installer(tmp_path):
    srv = Server()
    up = make(srv, tmp_path)
    up.check()
    srv.portal = True
    up.download()
    s = up.status()
    assert s["state"] == "error" and "Wi-Fi" in s["error"]
    assert not list((tmp_path / "updates").glob("ForLabsSetup-*"))  # страница не останется вместо установщика


def test_corrupted_download_is_deleted_and_redownloaded(tmp_path):
    srv = Server(sha="f" * 64)
    up = make(srv, tmp_path)
    up.check()
    up.download()
    assert up.status()["state"] == "error" and "повреждён" in up.status()["error"]
    assert not list((tmp_path / "updates").glob("ForLabsSetup-*"))
    srv.meta["sha256"] = hashlib.sha256(DATA).hexdigest()
    up.release.sha256 = srv.meta["sha256"]
    up.download()
    assert ready(up)


def test_disk_full_is_explained(tmp_path, monkeypatch):
    srv = Server()
    up = make(srv, tmp_path)
    up.check()

    class Full:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def write(self, b):
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(update_mod, "open", Full, raising=False)
    up.download()
    s = up.status()
    assert s["state"] == "error" and "места" in s["error"] and "МБ" in s["error"]


def test_missing_file_on_server_is_not_retried_forever(tmp_path):
    srv = Server()
    up = make(srv, tmp_path)
    up.check()
    srv.statuses = [404]
    up.download()
    assert up.status()["state"] == "error" and "не найдено" in up.status()["error"]
    assert len(srv.downloads) == 1


def test_installer_downloaded_before_restart_is_reused(tmp_path):
    srv = Server()
    up = make(srv, tmp_path)
    up.check()
    up.download()
    srv.downloads = []
    again = make(srv, tmp_path)
    again.check()
    again.download()
    assert ready(again) and srv.downloads == []  # не качает второй раз


def test_after_update_old_installer_is_removed(tmp_path):
    srv = Server(version="1.0.3")
    up = make(srv, tmp_path)
    up.check()
    up.download()
    assert list((tmp_path / "updates").iterdir())
    updated = make(srv, tmp_path, current="1.0.3")  # открылась уже новая версия
    assert updated.check() is None and not list((tmp_path / "updates").iterdir())


def test_double_click_downloads_once(tmp_path):
    srv = Server()
    gate = threading.Event()
    orig = srv.handler

    def slow(req):
        if str(req.url) != LATEST_JSON:
            gate.wait(2)
        return orig(req)

    srv.handler = slow
    up = make(srv, tmp_path)
    up.check()
    assert up.start_download() is True and up.start_download() is True
    gate.set()
    for _ in range(500):
        if up.status()["state"] == "ready":
            break
        time.sleep(0.01)
    assert ready(up) and len(srv.downloads) == 1


@pytest.mark.parametrize("meta", [{"version": "9.9.9"}, {"version": "9.9.9", "size": 10, "sha256": "x", "url": "u"},
                                  {"version": "9.9.9", "size": 10, "sha256": "a" * 64, "url": "http://plain"},
                                  {"version": "9.9.9", "size": 10, "sha256": "a" * 64,
                                   "url": "https://evil.example/ForLabsSetup.exe"}])
def test_strange_release_info_is_ignored(tmp_path, meta):
    srv = Server()
    srv.meta = meta
    assert make(srv, tmp_path).check() is None
