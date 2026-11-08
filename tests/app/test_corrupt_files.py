"""Выключение питания посреди записи, полный диск: обрезанные файлы не должны ронять приложение."""

import json

import pytest
from fastapi.testclient import TestClient

from forlabs.core.fs import atomic_write_text
from forlabs.core.settings import Settings
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

BROKEN = ["", "{", '{"mode": "local", "model": ', "\x00\x00\x00"]


def client():
    return TestClient(create_app(AppContext(settings=Settings(mode="local"), synchronous=True)),
                      base_url="http://127.0.0.1:8765")


@pytest.mark.parametrize("junk", BROKEN)
def test_broken_settings_file(tmp_home, junk):
    tmp_home.mkdir(parents=True, exist_ok=True)
    (tmp_home / "settings.json").write_text(junk, encoding="utf-8")
    ctx = AppContext()  # раньше — ValidationError, приложение не открывалось
    assert ctx.settings.model == Settings().model
    assert list(tmp_home.glob("settings.json.broken*")), "битый файл сохраняется рядом для разбора"


@pytest.mark.parametrize("junk", BROKEN)
def test_broken_subjects_file(tmp_home, junk):
    tmp_home.mkdir(parents=True, exist_ok=True)
    (tmp_home / "subjects.json").write_text(junk, encoding="utf-8")
    c = client()
    for url in ("/", "/settings", "/profiles", "/labs/new"):
        assert c.get(url, follow_redirects=False).status_code in (200, 303), url


def test_broken_profile_and_lab_are_skipped(tmp_home):
    from forlabs.core.models import LabState, Profile
    from forlabs.core.store import Store

    store = Store()
    store.save_profile(Profile(id="ivanov", full_name="Иванов Иван", title_name="И.И. Иванов"))
    store.save_lab(LabState(profile_id="ivanov", subject_id="ssp", number="4", status="parsed"))
    store.save_lab(LabState(profile_id="ivanov", subject_id="ssp", number="5", status="parsed"))
    (store.lab_dir("ivanov", "ssp", "5") / "state.json").write_text('{"profile_id": "iva', encoding="utf-8")
    bad = tmp_home / "profiles" / "broken.json"
    bad.write_text("", encoding="utf-8")
    assert [p.id for p in store.list_profiles()] == ["ivanov"]
    assert [s.number for s in store.list_labs("ivanov", "ssp")] == ["4"]
    assert store.load_lab("ivanov", "ssp", "5") is None
    assert client().get("/?profile=ivanov&subject=ssp").status_code == 200


def test_broken_session_file_means_signed_out(tmp_home):
    from forlabs.cloud.auth import CloudAuth
    from forlabs.cloud.config import DEFAULT

    auth = CloudAuth(DEFAULT, use_keyring=False)
    auth._session_file().parent.mkdir(parents=True, exist_ok=True)
    auth._session_file().write_text('{"access_token": "x", "refr', encoding="utf-8")
    auth.load_session()
    assert auth.session is None


def test_atomic_write_keeps_old_file_on_failure(tmp_path, monkeypatch):
    f = tmp_path / "a.json"
    atomic_write_text(f, json.dumps({"v": 1}))
    import os

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(f, json.dumps({"v": 2}))
    assert json.loads(f.read_text()) == {"v": 1}  # старое содержимое цело
    assert not list(tmp_path.glob("*.tmp"))
