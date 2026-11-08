"""Админ-панель: страницы и действия поверх фейкового Supabase (настоящий CloudStore)."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / "cloud"))
from test_cloud import CFG, UID, FakeSupabase  # noqa: E402

from forlabs.cloud.auth import CloudAuth  # noqa: E402
from forlabs.cloud.store import CloudStore  # noqa: E402
from forlabs.core.settings import Settings  # noqa: E402
from forlabs.ui.app import create_app  # noqa: E402
from forlabs.ui.context import AppContext  # noqa: E402

M = "99999999-8888-7777-6666-555555555555"


@pytest.fixture
def fake():
    f = FakeSupabase()
    f.tables["members"][0].update({"created_at": "2026-10-01T09:00:00+00:00", "access_until": None, "note": "",
                                   "allow_shared_key": True, "last_seen_at": None})
    f.tables["members"].append({"user_id": M, "email": "anna@example.com", "display_name": "Анна", "role": "member",
                                "approved": True, "access_until": None, "note": "", "allow_shared_key": True,
                                "last_seen_at": "2026-11-07T18:00:00+00:00", "created_at": "2026-11-01T09:00:00+00:00"})
    f.keys[M] = "5678"
    f.balance = {M: 92011485, "shared": 5000}
    today = datetime.now(timezone(timedelta(hours=3))).date()
    f.daily = [{"day": str(today - timedelta(days=1)), "user_id": M, "tokens": 27000},
               {"day": str(today), "user_id": M, "tokens": 3000}]
    f.by_lab = [{"lab": "anna/ssp/4", "tokens": 27584, "calls": 9, "last_at": "2026-11-07T11:00:00+00:00"}]
    f.tables["labs"] = [{"owner": M, "profile_id": "anna", "subject_id": "ssp", "number": "4", "status": "final",
                         "created_at": "2026-11-07T10:00:00+00:00", "updated_at": "2026-11-07T11:00:00+00:00"}]
    f.tables["student_profiles"] = [{"owner": M, "id": "anna", "data": {
        "id": "anna", "full_name": "Иванова Анна Петровна", "title_name": "А.П. Иванова", "group": "РС02-23"}}]
    f.tables["member_events"] = [{"id": 1, "user_id": M, "actor": UID, "kind": "key_set", "detail": {"last4": "5678"},
                                  "created_at": "2026-11-05T10:00:00+00:00"}]
    return f


def make_client(fake, tmp_home, me=UID):
    auth = CloudAuth(CFG, http=httpx.Client(transport=httpx.MockTransport(fake)), use_keyring=False)
    auth.sign_in("a@b.c", "верный")
    if me != UID:  # войти «участником»: фейк всегда отдаёт UID, поэтому меняем роль строки UID
        fake.tables["members"][0]["role"] = "member"
    ctx = AppContext(settings=Settings(mode="cloud"), synchronous=True)
    ctx.auth = auth
    ctx._store = CloudStore(auth)
    return TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")


@pytest.fixture
def admin(fake, tmp_home):
    return make_client(fake, tmp_home)


def test_admin_page_lists_members_and_charts(admin):
    page = admin.get("/admin").text
    assert "Иванова Анна Петровна" in page and "anna@example.com" in page
    assert "••••5678" in page and "<svg" in page
    assert "30 тыс." in page  # расход за 30 дней
    assert 'href="/admin/u/' + M + '"' in page


def test_member_cannot_open_admin(fake, tmp_home):
    client = make_client(fake, tmp_home, me="member")
    for url in ("/admin", f"/admin/u/{M}", "/admin/balances"):
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/", url
    r = client.post(f"/admin/u/{M}/access", data={"action": "unlimited"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_admin_balances_json(admin):
    data = admin.get("/admin/balances").json()
    assert data[M]["text"] == "92 млн" and "лаб" in data[M]["labs"]
    assert data["shared"]["text"] == "5 000"


def test_admin_access_actions(admin, fake):
    row = fake.tables["members"][1]
    url = f"/admin/u/{M}/access"
    r = admin.post(url, data={"action": "extend30"}, follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    until = datetime.fromisoformat(row["access_until"])
    assert abs(until - datetime.now(timezone.utc) - timedelta(days=30)) < timedelta(minutes=1)
    admin.post(url, data={"action": "until", "until": "2026-12-12"})
    assert row["access_until"] == "2026-12-12T23:59:59+03:00"
    admin.post(url, data={"action": "unlimited"})
    assert row["access_until"] is None
    admin.post(url, data={"action": "revoke"})
    assert datetime.fromisoformat(row["access_until"]) <= datetime.now(timezone.utc)
    admin.post(url, data={"action": "disable"})
    assert row["approved"] is False
    admin.post(url, data={"action": "approve"})
    assert row["approved"] is True
    r = admin.post(url, data={"action": "until", "until": "не дата"}, follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_admin_set_key_validates(admin, fake):
    url = f"/admin/u/{M}/key"
    r = admin.post(url, data={"action": "set", "api_key": "  abc "}, follow_redirects=False)
    assert "err=" in r.headers["location"] and fake.keys[M] == "5678"
    admin.post(url, data={"action": "set", "api_key": "sk-new-key-0000abcd"})
    assert fake.keys[M] == "abcd"
    admin.post(url, data={"action": "clear"})
    assert M not in fake.keys
    admin.post(url, data={"action": "shared_off"})
    assert fake.tables["members"][1]["allow_shared_key"] is False


def test_admin_note_and_name(admin, fake):
    admin.post(f"/admin/u/{M}/note", data={"display_name": "Анна И.", "note": "оплатила за ноябрь"})
    row = fake.tables["members"][1]
    assert row["display_name"] == "Анна И." and fake.tables["member_notes"][0]["note"] == "оплатила за ноябрь"
    assert "оплатила за ноябрь" in admin.get(f"/admin/u/{M}").text


def test_admin_member_page(admin):
    page = admin.get(f"/admin/u/{M}").text
    assert "Иванова Анна Петровна" in page and "РС02-23" in page
    assert "ЛР 4" in page and "28 тыс." in page  # токены лабы
    assert "Ключ выдан" in page  # история
    assert "<svg" in page


def test_admin_unknown_member(admin):
    r = admin.get("/admin/u/00000000-0000-0000-0000-000000000000", follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]


def test_admin_download_report(admin, fake):
    from forlabs.cloud.store import storage_key

    fake.objects[storage_key(M, "anna", "ssp", "4", "report.docx")] = b"PK-docx"
    r = admin.get(f"/admin/u/{M}/lab/anna/ssp/4.docx")
    assert r.status_code == 200 and r.content == b"PK-docx"
    r = admin.get(f"/admin/u/{M}/lab/anna/ssp/5.docx", follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]


def test_users_redirects_to_admin(admin):
    r = admin.get("/users", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"


def test_member_sees_subscription_in_header(fake, tmp_home):
    client = make_client(fake, tmp_home, me="member")
    me = fake.tables["members"][0]
    me["access_until"] = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    page = client.get("/settings").text
    assert "Подписка до" in page and "sub-expiring" in page
    me["access_until"] = "2026-01-01T00:00:00+00:00"
    page = client.get("/settings").text
    assert "Подписка закончилась 01.01.2026" in page


def test_member_sees_own_balance(fake, tmp_home):
    client = make_client(fake, tmp_home, me="member")
    fake.balance[UID] = 1500
    assert "1 500" in client.get("/settings").text


def test_member_color_is_the_same_on_overview_and_card(admin):
    import re

    overview = admin.get("/admin").text
    row = overview[overview.index('data-href="/admin/u/' + M):]
    color = re.search(r'class="dot" style="background:(#[0-9a-f]{6})"', row).group(1)
    card = admin.get(f"/admin/u/{M}").text
    assert f'fill="{color}"' in card


def test_member_colors_follow_join_order_not_admin_first():
    from forlabs.cloud.store import Member
    from forlabs.ui.admin_routes import member_colors
    from forlabs.ui.charts import SERIES_COLORS

    members = [Member(user_id="adm", role="admin", approved=True, display_name="Админ"),
               Member(user_id="a", approved=True, display_name="А"), Member(user_id="b", approved=True, display_name="Б")]
    colors = member_colors(members)
    assert colors["a"][1] == SERIES_COLORS[0] and colors["b"][1] == SERIES_COLORS[1] and colors["adm"][1] == SERIES_COLORS[2]
    many = members + [Member(user_id=f"m{i}", approved=True, display_name=f"М{i}") for i in range(4)]
    colors = member_colors(many)
    assert colors["m3"] == ("Остальные", SERIES_COLORS[4]) and colors["a"][1] == SERIES_COLORS[0]


def test_pending_member_card_offers_approve_first(admin, fake):
    fake.tables["members"][1]["approved"] = False
    page = admin.get(f"/admin/u/{M}").text
    assert page.index("Одобрить аккаунт") < page.index("+7 дней")
    assert "ждёт одобрения" in admin.get("/admin").text


def test_admin_balances_offline_vs_no_key(admin, fake):
    fake.balance = {}
    fake.balance_errors = {M: "provider 502", UID: "no key"}
    data = admin.get("/admin/balances").json()
    assert data[M]["text"] == "нет связи" and data[UID]["text"] == "—"
    fake.functions_down = True
    data = admin.get("/admin/balances").json()
    assert data[M]["text"] == "нет связи" and data["shared"]["text"] == "нет связи"


def test_member_settings_offline_is_not_no_key(fake, tmp_home):
    client = make_client(fake, tmp_home, me="member")
    fake.functions_down = True
    page = client.get("/settings").text
    assert "нет связи" in page and "ещё не выдан" not in page
    fake.functions_down = False
    fake.balance_errors = {UID: "no key"}
    assert "ещё не выдан" in client.get("/settings").text


def test_admin_cannot_change_own_access(admin, fake):
    r = admin.post(f"/admin/u/{UID}/access", data={"action": "revoke"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and fake.tables["members"][0]["access_until"] is None


def test_card_shows_usage_not_tied_to_a_lab(admin, fake):
    fake.by_lab = [{"lab": "anna/ssp/4", "tokens": 27584, "calls": 9, "last_at": "2026-11-07T11:00:00+00:00"},
                   {"lab": "", "tokens": 1200, "calls": 1, "last_at": "2026-11-07T09:00:00+00:00"},
                   {"lab": "anna/ssp/удалена", "tokens": 4000, "calls": 2, "last_at": "2026-11-06T09:00:00+00:00"}]
    page = admin.get(f"/admin/u/{M}").text
    assert "Без привязки к лабе" in page and "5 200" in page
