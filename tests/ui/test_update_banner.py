"""Кнопка «Обновить» в приложении: видна при новой версии, качает, ставит только когда лаба не выполняется."""

import hashlib
import re
import json

import httpx
from fastapi.testclient import TestClient

from forlabs.core.settings import Settings
from forlabs.core.update import DOWNLOADS, LATEST_JSON, Updater
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

SETUP = b"MZ" + b"\x01" * 3000


def _updater(tmp_path, version="9.0.0"):
    meta = {"version": version, "size": len(SETUP), "sha256": hashlib.sha256(SETUP).hexdigest(),
            "url": f"{DOWNLOADS}v{version}/ForLabsSetup.exe"}

    def handler(req):
        if str(req.url) == LATEST_JSON:
            return httpx.Response(200, content=json.dumps(meta).encode())
        return httpx.Response(200, content=SETUP)

    return Updater(current="1.0.2", http=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
                   folder=tmp_path / "upd", enabled=True)


def _client(tmp_home, tmp_path):
    ctx = AppContext(settings=Settings(mode="local", tour_done=True), prefer_word=False, synchronous=True)
    ctx.updater = _updater(tmp_path)
    started, quits = [], []
    ctx.spawn_installer = started.append
    ctx.quit = lambda: quits.append(1)
    return TestClient(create_app(ctx), base_url="http://127.0.0.1:8765"), ctx, started, quits


def test_card_hidden_when_nothing_new_and_absent_when_off(tmp_home, tmp_path):
    client, ctx, _, _ = _client(tmp_home, tmp_path)
    ctx.updater = _updater(tmp_path, version="1.0.2")  # та же версия
    page = client.get("/").text
    assert re.search(r'data-update data-state=""\s+data-version=""\s+hidden', page)  # есть, но скрыта
    ctx.updater = Updater(enabled=False)  # не установленная программа (разработка) — карточки нет вовсе
    assert "data-update" not in client.get("/").text


def test_later_hides_card_until_restart(tmp_home, tmp_path):
    client, ctx, _, _ = _client(tmp_home, tmp_path)
    ctx.updater.check()
    assert "Доступно обновление" in client.get("/").text and " hidden>" not in client.get("/").text.split("data-update ")[1][:200]
    assert client.post("/update/later").json()["ok"] is True
    assert client.get("/update/status").json()["dismissed"] is True
    assert " hidden>" in client.get("/").text.split("data-update ")[1][:200]


def test_installer_blocked_by_antivirus_keeps_app_open(tmp_home, tmp_path):
    client, ctx, _, quits = _client(tmp_home, tmp_path)

    def blocked(cmd):
        raise OSError("Доступ запрещён")

    ctx.spawn_installer = blocked
    ctx.updater.check()
    client.post("/update/start")
    r = client.post("/update/install")
    assert r.status_code == 500 and "не запустился" in r.json()["error"] and quits == []


def test_banner_download_and_install_restarts(tmp_home, tmp_path):
    client, ctx, started, quits = _client(tmp_home, tmp_path)
    ctx.updater.check()
    page = client.get("/").text
    assert "data-update" in page and "9.0.0" in page and "Обновить" in page and "Позже" in page
    assert client.post("/update/start").json()["ok"] is True
    for _ in range(200):
        if client.get("/update/status").json()["state"] == "ready":
            break
    assert client.get("/update/status").json()["state"] == "ready"
    ctx.running.add("p/s/7")  # идёт работа над лабой — не перезапускать посреди неё
    r = client.post("/update/install")
    assert r.status_code == 409 and "лаб" in r.json()["error"] and started == []
    ctx.running.clear()
    r = client.post("/update/install")
    assert r.json()["ok"] is True
    assert started and started[0][1:3] == ["--update", "--wait-pid"]
    assert quits == [1]  # окно закрывается, установщик дождётся и откроет новую версию


def test_install_before_download_refused(tmp_home, tmp_path):
    client, ctx, started, _ = _client(tmp_home, tmp_path)
    ctx.updater.check()
    r = client.post("/update/install")
    assert r.status_code == 409 and started == []
