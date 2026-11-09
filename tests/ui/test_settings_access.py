"""Настройки: режим, модель, адрес API и ключ меняет только администратор; в собранной программе только облако;
до входа настройки закрыты; на странице входа нет «Настроек подключения» и просьбы подтвердить почту."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_admin import admin, fake, make_client  # noqa: E402, F401

from forlabs.core.settings import load_settings  # noqa: E402


def test_member_sees_only_programs_and_subscription(fake, tmp_home):
    c = make_client(fake, tmp_home, me="member")
    page = c.get("/settings").text
    for hidden in ('name="mode"', 'name="api_key"', 'name="base_url"', 'name="model"', "Локально"):
        assert hidden not in page, hidden
    assert 'name="soffice_path"' in page and 'name="tesseract_path"' in page and "Пройти обучение заново" in page


def test_member_cannot_change_mode_key_or_api_by_post(fake, tmp_home, monkeypatch):
    import forlabs.ui.app as app_mod

    stored = []
    monkeypatch.setattr(app_mod, "set_api_key", lambda k: stored.append(k) or True)
    c = make_client(fake, tmp_home, me="member")
    before = load_settings()
    c.post("/settings", data={"mode": "local", "api_key": "sk-evil-123456", "base_url": "https://evil.example/v1",
                              "model": "other-model", "tesseract_path": r"C:\T\tesseract.exe"})
    s = load_settings()
    assert s.mode == "cloud" and s.base_url == before.base_url and s.model == before.model and stored == []
    assert s.tesseract_path == r"C:\T\tesseract.exe"  # своё на этом ПК — можно


def test_admin_sees_model_choice(admin):
    page = admin.get("/settings").text
    assert 'name="model"' in page


def test_frozen_build_is_cloud_only(fake, tmp_home, monkeypatch):
    from forlabs import version
    from forlabs.core.settings import Settings, save_settings

    tmp_home.mkdir(parents=True, exist_ok=True)
    save_settings(Settings(mode="local"))
    monkeypatch.setattr(version, "frozen", lambda: True)
    assert load_settings().mode == "cloud"
    page = make_client(fake, tmp_home).get("/settings").text  # даже администратору — без «Локально»
    assert 'value="local"' not in page and 'name="api_key"' not in page and 'name="base_url"' not in page
    assert 'name="model"' in page  # модель администратор выбирает


def test_settings_closed_before_login_and_login_page_is_clean(fake, tmp_home):
    import httpx
    from fastapi.testclient import TestClient

    from forlabs.cloud.auth import CloudAuth
    from forlabs.cloud.store import CloudStore
    from forlabs.core.settings import Settings
    from forlabs.ui.app import create_app
    from forlabs.ui.context import AppContext
    from test_cloud import CFG

    auth = CloudAuth(CFG, http=httpx.Client(transport=httpx.MockTransport(fake)), use_keyring=False)
    ctx = AppContext(settings=Settings(mode="cloud"), synchronous=True)
    ctx.auth, ctx._store = auth, CloudStore(auth)
    c = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    r = c.get("/settings", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    for tab in ("/login", "/login?tab=register"):
        page = c.get(tab).text
        assert 'href="/settings"' not in page and "подтверди почту" not in page
