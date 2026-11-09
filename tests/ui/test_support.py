"""Поддержка: студент пишет из приложения, администратор отвечает (из Telegram или админки), Telegram подключается."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_admin import M, admin, fake, make_client  # noqa: E402, F401
from test_cloud import UID  # noqa: E402

from forlabs.core.settings import Settings  # noqa: E402


def member_client(fake, tmp_home):
    return make_client(fake, tmp_home, me="member")


def test_member_writes_and_sees_thread(fake, tmp_home, monkeypatch):
    import forlabs.cloud.support as support_mod

    monkeypatch.setattr(support_mod, "UNREAD_TTL", 0)  # значок без задержки кэша
    c = member_client(fake, tmp_home)
    page = c.get("/support").text
    assert 'action="/support"' in page and "Поддержка" in page
    r = c.post("/support", data={"text": "Не открывается лаба 7"}, follow_redirects=False)
    assert r.status_code == 303
    assert fake.tables["support_messages"][0]["text"] == "Не открывается лаба 7"
    fake.tables["support_messages"].append({"id": 2, "user_id": UID, "from_admin": True, "text": "Перезапусти ForLabs",
                                            "created_at": "2026-11-08T12:30:00+00:00", "read_at": None})
    assert 'class="support-badge"' in c.get("/settings").text  # непрочитанный ответ виден в шапке
    page = c.get("/support").text
    assert "Не открывается лаба 7" in page and "Перезапусти ForLabs" in page
    assert fake.tables["support_messages"][1]["read_at"]  # открыл — прочитано
    assert 'class="support-badge"' not in c.get("/settings").text


def test_empty_and_rate_limited_messages(fake, tmp_home):
    c = member_client(fake, tmp_home)
    r = c.post("/support", data={"text": "   "}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    for i in range(20):
        c.post("/support", data={"text": f"сообщение {i}"})
    r = c.post("/support", data={"text": "ещё"}, follow_redirects=False)
    from urllib.parse import unquote

    assert "Слишком много сообщений" in unquote(r.headers["location"])


def test_pending_member_can_reach_support(fake, tmp_home):
    c = member_client(fake, tmp_home)
    fake.tables["members"][0]["approved"] = False
    assert 'href="/support"' in c.get("/").text
    assert c.get("/support").status_code == 200


def test_local_mode_explains_support_needs_cloud(tmp_home):
    from fastapi.testclient import TestClient

    from forlabs.ui.app import create_app
    from forlabs.ui.context import AppContext

    tmp_home.mkdir(parents=True, exist_ok=True)
    c = TestClient(create_app(AppContext(settings=Settings(mode="local"))), base_url="http://127.0.0.1:8765")
    assert "облачном режиме" in c.get("/support").text


def test_admin_sees_threads_and_replies(admin, fake):
    fake.tables["support_messages"] = [
        {"id": 1, "user_id": M, "from_admin": False, "text": "Дайте доступ", "created_at": "2026-11-08T10:00:00+00:00",
         "read_at": None}]
    page = admin.get("/admin/support").text
    assert "Анна" in page and "Дайте доступ" in page and f'href="/admin/support/{M}"' in page
    page = admin.get(f"/admin/support/{M}").text
    assert "Дайте доступ" in page
    assert fake.tables["support_messages"][0]["read_at"]  # администратор прочитал
    r = admin.post(f"/admin/support/{M}", data={"text": "Открыл пробный"}, follow_redirects=False)
    assert r.status_code == 303
    reply = fake.tables["support_messages"][-1]
    assert reply == {**reply, "user_id": M, "from_admin": True, "text": "Открыл пробный"}


def test_admin_connects_telegram(admin, fake):
    page = admin.get("/admin/support").text
    assert "Telegram не подключён" in page and 'action="/admin/support/setup"' in page
    page = admin.post("/admin/support/setup").text
    assert "https://t.me/forlabs_help_bot?start=Ab12Cd34" in page
    fake.support_bound = True
    assert "Telegram подключён" in admin.get("/admin/support").text


def test_admin_telegram_without_secrets_explains(admin, fake):
    fake.support_secrets = False
    page = admin.post("/admin/support/setup").text
    assert "TELEGRAM_BOT_TOKEN" in page and "TELEGRAM_WEBHOOK_SECRET" in page


def test_member_cannot_open_admin_support(fake, tmp_home):
    c = member_client(fake, tmp_home)
    r = c.get("/admin/support", follow_redirects=False)
    assert r.status_code in (303, 307) or "Админ" not in r.text


def test_admin_page_links_to_support(admin):
    assert 'href="/admin/support"' in admin.get("/admin").text


def test_support_before_server_update_explains(fake, tmp_home):
    from urllib.parse import unquote

    c = member_client(fake, tmp_home)
    fake.no_support = True
    assert "не включена на сервере" in c.get("/support").text
    r = c.post("/support", data={"text": "привет"}, follow_redirects=False)
    assert "не включена на сервере" in unquote(r.headers["location"])
    assert c.get("/settings").status_code == 200  # остальное работает


def test_unread_badge_is_cached_between_pages(fake, tmp_home):
    c = member_client(fake, tmp_home)
    c.get("/settings")
    before = sum(1 for r in fake.requests if r.url.path.endswith("/support_messages"))
    c.get("/settings")
    c.get("/profiles")
    assert sum(1 for r in fake.requests if r.url.path.endswith("/support_messages")) == before


def test_signup_without_email_confirmation_logs_in_and_shows_pending(fake, tmp_home):
    import httpx
    from fastapi.testclient import TestClient

    from forlabs.cloud.auth import CloudAuth
    from forlabs.cloud.store import CloudStore
    from forlabs.ui.app import create_app
    from forlabs.ui.context import AppContext
    from test_cloud import CFG

    fake.autoconfirm = True
    fake.tables["members"][0].update({"role": "member", "approved": False})
    auth = CloudAuth(CFG, http=httpx.Client(transport=httpx.MockTransport(fake)), use_keyring=False)
    ctx = AppContext(settings=Settings(mode="cloud"), synchronous=True)
    ctx.auth, ctx._store = auth, CloudStore(auth)
    c = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    r = c.post("/register", data={"email": "a@b.c", "password": "верный", "name": "Аня"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/?") and "подтверди" not in r.headers["location"]
    page = c.get("/").text
    assert "ждёт одобрения" in page and 'href="/support"' in page


# быстрые переходы: данные участника не спрашиваются у облака на каждой странице
def test_pages_reuse_member_data(fake, tmp_home):
    c = member_client(fake, tmp_home)
    c.get("/settings")
    before = sum(1 for r in fake.requests if r.url.path.endswith("/members"))
    for url in ("/settings", "/profiles", "/support", "/settings"):
        c.get(url)
    assert sum(1 for r in fake.requests if r.url.path.endswith("/members")) == before


def test_pending_member_sees_approval_without_waiting(fake, tmp_home):
    c = member_client(fake, tmp_home)
    fake.tables["members"][0]["approved"] = False
    c.get("/")
    fake.tables["members"][0]["approved"] = True  # администратор одобрил
    assert "ждёт одобрения" not in c.get("/").text


def test_admin_bot_check_explains_each_problem(admin, fake):
    fake.support_check = {"secrets": True, "bound": False, "code_active": False, "token_ok": False, "bot": None}
    r = admin.get("/admin/support/check").json()
    bad = " ".join(i["text"] for i in r["items"] if not i["ok"])
    assert r["bound"] is False and "токен" in bad and "@BotFather" in bad
    fake.support_check = {"secrets": True, "bound": False, "code_active": False, "token_ok": True,
                          "bot": "forlabs_help_bot", "webhook_ok": False, "webhook_error": None}
    bad = " ".join(i["text"] for i in admin.get("/admin/support/check").json()["items"] if not i["ok"])
    assert "Подключить Telegram" in bad
    fake.support_check = {"secrets": True, "bound": False, "code_active": True, "token_ok": True,
                          "bot": "forlabs_help_bot", "webhook_ok": True, "webhook_error": None}
    texts = " ".join(i["text"] for i in admin.get("/admin/support/check").json()["items"])
    assert "«Запустить»" in texts
    fake.support_check = {"secrets": True, "bound": True, "code_active": False, "token_ok": True,
                          "bot": "forlabs_help_bot", "webhook_ok": True, "webhook_error": "Connection timed out"}
    r = admin.get("/admin/support/check").json()
    assert r["bound"] is True and any("Connection timed out" in i["text"] for i in r["items"])
    fake.support_secrets = False
    fake.support_check = {"secrets": False, "bound": False, "code_active": False}
    bad = " ".join(i["text"] for i in admin.get("/admin/support/check").json()["items"] if not i["ok"])
    assert "TELEGRAM_BOT_TOKEN" in bad
