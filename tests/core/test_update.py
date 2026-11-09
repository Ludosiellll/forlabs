"""Обновление из приложения: проверка новой версии, загрузка установщика с проверкой, запуск установки."""

import hashlib
import json

import httpx

from forlabs.core.update import DOWNLOADS, LATEST_JSON, Updater, parse_version

SETUP = b"MZ" + b"\x00" * 5000  # «установщик»


def _http(version="9.9.9", data=SETUP, sha=None, calls=None, status=200):
    meta = {"version": version, "size": len(data), "sha256": sha or hashlib.sha256(data).hexdigest(),
            "url": f"{DOWNLOADS}v{version}/ForLabsSetup.exe"}

    def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(req.url))
        if str(req.url) == LATEST_JSON:
            return httpx.Response(status, content=json.dumps(meta).encode())
        if str(req.url).endswith("ForLabsSetup.exe"):
            return httpx.Response(200, content=data)
        return httpx.Response(404)

    return lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_versions_compare_as_numbers():
    assert parse_version("1.0.10") > parse_version("1.0.9") > parse_version("1.0")
    assert parse_version("v1.2.0") == (1, 2, 0) and parse_version("мусор") == ()


def test_newer_release_is_offered_same_or_older_is_not(tmp_path):
    up = Updater(current="1.0.2", http=_http("1.0.3"), folder=tmp_path, enabled=True)
    assert up.check().version == "1.0.3" and up.status()["state"] == "available"
    for v in ("1.0.2", "1.0.1"):
        up = Updater(current="1.0.2", http=_http(v), folder=tmp_path, enabled=True)
        assert up.check() is None and up.status()["state"] == "idle"


def test_disabled_outside_installed_program_makes_no_requests(tmp_path):
    calls = []
    up = Updater(current="1.0.2", http=_http("2.0.0", calls=calls), folder=tmp_path, enabled=False)
    assert up.check() is None and calls == [] and up.status()["state"] == "off"


def test_no_internet_or_private_repo_is_silent(tmp_path):
    up = Updater(current="1.0.2", http=_http(status=404), folder=tmp_path, enabled=True)
    assert up.check() is None and up.status()["state"] == "idle"


def test_download_verifies_checksum_and_size(tmp_path):
    up = Updater(current="1.0.2", http=_http("1.0.3"), folder=tmp_path, enabled=True)
    up.check()
    up.download()
    s = up.status()
    assert s["state"] == "ready" and s["percent"] == 100
    assert up.installer.read_bytes() == SETUP and not list(tmp_path.glob("*.part"))
    bad = Updater(current="1.0.2", http=_http("1.0.3", sha="0" * 64), folder=tmp_path / "b", enabled=True)
    bad.check()
    bad.download()
    s = bad.status()
    assert s["state"] == "error" and "повреждён" in s["error"]
    assert not list((tmp_path / "b").glob("*.exe*"))  # битый файл не остаётся и не запускается


def test_install_command_waits_for_this_program(tmp_path):
    up = Updater(current="1.0.2", http=_http("1.0.3"), folder=tmp_path, enabled=True)
    up.check()
    up.download()
    started = []
    up.install(pid=4242, spawn=lambda cmd: started.append(cmd))
    assert started == [[str(up.installer), "--update", "--wait-pid", "4242"]]
    assert up.status()["state"] == "installing"
