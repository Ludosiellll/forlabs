"""Мост окна установщика (SetupApi): значения по умолчанию, проверка папки, установка в фоне, отмена, запуск."""

import time
from pathlib import Path

from forlabs.installer import core
from forlabs.installer.app import SetupApi, silent


def _wait(api, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        s = api.poll()
        if s["state"] != "running":
            return s
        time.sleep(0.01)
    raise AssertionError("установка не закончилась")


def test_defaults_for_fresh_install(payload, shell, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    d = SetupApi(payload, shell).defaults()
    assert d["target"] == str(tmp_path / "local" / "Programs" / "ForLabs")
    assert d["installed"] is None and d["desktop"] is True and d["version"] == core.__version__
    assert [c["name"] for c in d["components"]][0] == "Программа ForLabs"
    assert d["size_mb"] >= 1


def test_defaults_for_update_use_existing_location(payload, shell, tmp_path):
    core.install(payload, tmp_path / "Где-то" / "ForLabs", shell=shell, desktop=False)
    d = SetupApi(payload, shell).defaults()
    assert d["installed"] == core.__version__ and d["target"] == str(tmp_path / "Где-то" / "ForLabs")
    assert d["desktop"] is False


def test_defaults_report_broken_payload(tmp_path, shell):
    (tmp_path / "p.zip").write_bytes(b"x")
    assert "повреждён" in SetupApi(tmp_path / "p.zip", shell).defaults()["error"]


def test_browse_appends_program_folder(payload, shell, tmp_path):
    api = SetupApi(payload, shell, folder_picker=lambda initial: str(tmp_path / "Программы"))
    assert api.browse("x") == str(tmp_path / "Программы" / "ForLabs")
    api = SetupApi(payload, shell, folder_picker=lambda initial: str(tmp_path / "ForLabs"))
    assert api.browse("x") == str(tmp_path / "ForLabs")
    assert SetupApi(payload, shell, folder_picker=lambda initial: None).browse("x") is None


def test_start_rejects_bad_folder_without_starting(payload, shell):
    r = SetupApi(payload, shell).start("ForLabs", True)
    assert r["ok"] is False and "полный путь" in r["error"]


def test_install_runs_in_background_and_reports_components(payload, shell, tmp_path):
    api = SetupApi(payload, shell)
    assert api.start(str(tmp_path / "ForLabs"), False) == {"ok": True}
    s = _wait(api)
    assert s["state"] == "done" and s["percent"] == 100 and s["fractions"] == [1, 1, 1, 1]
    assert (tmp_path / "ForLabs" / "ForLabs.exe").exists()
    assert shell.desktop / "ForLabs.lnk" not in shell.shortcuts


def test_install_error_is_shown_and_retry_works(payload, shell, tmp_path):
    busy = tmp_path / "Занято" / "ForLabs"
    busy.mkdir(parents=True)
    (busy / "чужое.txt").write_text("x")
    api = SetupApi(payload, shell)
    r = api.start(str(busy), True)
    assert r["ok"] is False and "не пуста" in r["error"]
    assert api.start(str(tmp_path / "Другая" / "ForLabs"), True)["ok"]
    assert _wait(api)["state"] == "done"


def test_cancel_stops_install(payload, shell, tmp_path, monkeypatch):
    api = SetupApi(payload, shell)
    api.cancel()  # отмена до старта не мешает следующей установке
    real = core._extract_one

    def slow(*a):
        api.cancel()
        real(*a)

    monkeypatch.setattr(core, "_extract_one", slow)
    api.start(str(tmp_path / "ForLabs"), True)
    s = _wait(api)
    assert s["state"] == "error" and "отменена" in s["error"]
    assert not (tmp_path / "ForLabs").exists()


def test_finish_launches_installed_program(payload, shell, tmp_path):
    launched = []
    api = SetupApi(payload, shell, launcher=launched.append)
    api.start(str(tmp_path / "ForLabs"), True)
    _wait(api)
    assert api.finish(True)["ok"] and launched == [tmp_path / "ForLabs" / "ForLabs.exe"]
    api2 = SetupApi(payload, shell, launcher=launched.append)
    assert api2.finish(True)["ok"] and len(launched) == 1  # не установлено — нечего запускать


def test_silent_install_writes_log(payload, shell, tmp_path):
    log = tmp_path / "setup.log"
    code = silent(payload, ["--silent", "--target", str(tmp_path / "ForLabs"), "--no-desktop", "--log", str(log)],
                  shell=shell)
    assert code == 0 and "установлен" in log.read_text("utf-8")
    assert (tmp_path / "ForLabs" / "ForLabs.exe").exists()
    assert shell.desktop / "ForLabs.lnk" not in shell.shortcuts


def test_silent_install_failure_code(tmp_path, shell):
    (tmp_path / "p.zip").write_bytes(b"x")
    log = tmp_path / "setup.log"
    assert silent(tmp_path / "p.zip", ["--silent", "--target", str(tmp_path / "F"), "--log", str(log)], shell=shell) == 1
    assert "повреждён" in log.read_text("utf-8")


def test_page_has_no_external_resources():
    html = (Path(core.__file__).parent / "web" / "setup.html").read_text("utf-8")
    assert "http://" not in html and "https://" not in html  # установка без интернета
    assert "pywebviewready" in html and "pywebview-drag-region" in html
