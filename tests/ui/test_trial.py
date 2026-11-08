"""Пробный доступ: администратор выдаёт N лаб × M сообщений чата на лабу и любое число дней; студент видит остаток."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_admin import M, admin, fake, make_client  # noqa: E402, F401

from forlabs.core.llm import LLM, LLMError  # noqa: E402


def _now():
    return datetime.now(timezone.utc)


def test_admin_grants_trial(admin, fake):
    row = fake.tables["members"][1]
    row["approved"] = False
    r = admin.post(f"/admin/u/{M}/access", data={"action": "trial", "labs": "3", "chat": "5", "days": "7"},
                   follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    assert row["trial_labs"] == 3 and row["trial_chat"] == 5 and row["approved"] is True
    assert abs(datetime.fromisoformat(row["trial_since"]) - _now()) < timedelta(minutes=1)
    assert abs(datetime.fromisoformat(row["access_until"]) - _now() - timedelta(days=7)) < timedelta(minutes=1)


def test_admin_trial_validates_numbers(admin, fake):
    row = fake.tables["members"][1]
    for data in ({"labs": "0", "chat": "5", "days": "7"}, {"labs": "3", "chat": "-1", "days": "7"},
                 {"labs": "три", "chat": "5", "days": "7"}, {"labs": "3", "chat": "5", "days": "0"}):
        r = admin.post(f"/admin/u/{M}/access", data={"action": "trial", **data}, follow_redirects=False)
        assert "err=" in r.headers["location"], data
    assert row.get("trial_labs") is None


def test_admin_full_access_removes_limits(admin, fake):
    row = fake.tables["members"][1]
    row.update({"trial_labs": 3, "trial_chat": 5})
    admin.post(f"/admin/u/{M}/access", data={"action": "full"})
    assert row["trial_labs"] is None and row["trial_chat"] is None


def test_admin_extends_by_any_number_of_days(admin, fake):
    row = fake.tables["members"][1]
    admin.post(f"/admin/u/{M}/access", data={"action": "extend", "days": "12"})
    assert abs(datetime.fromisoformat(row["access_until"]) - _now() - timedelta(days=12)) < timedelta(minutes=1)
    for bad in ("0", "5000", "x", ""):
        r = admin.post(f"/admin/u/{M}/access", data={"action": "extend", "days": bad}, follow_redirects=False)
        assert "err=" in r.headers["location"], bad


def test_card_has_days_field_and_trial_form(admin):
    page = admin.get(f"/admin/u/{M}").text
    assert 'name="days"' in page and 'value="extend"' in page and 'value="trial"' in page
    assert "+7 дней" not in page


def test_card_shows_trial_usage(admin, fake):
    fake.tables["members"][1].update({"trial_labs": 3, "trial_chat": 5, "trial_since": "2026-11-01T00:00:00+00:00"})
    fake.trial = [{"lab": "anna/ssp/4", "calls": 9, "chats": 2, "has_chat": False},
                  {"lab": "", "calls": 1, "chats": 0, "has_chat": False}]
    page = admin.get(f"/admin/u/{M}").text
    assert "Пробный доступ" in page and "лаб 1 из 3" in page
    assert "ЛР 4" in page and "правок 2 из 5" in page


def test_member_sees_trial_in_header(fake, tmp_home):
    fake.tables["members"][0].update({"trial_labs": 3, "trial_chat": 5, "access_until": (_now() + timedelta(days=5)).isoformat(),
                                      "trial_since": _now().isoformat()})
    fake.trial = [{"lab": "p/s/1", "calls": 4, "chats": 1, "has_chat": False}]
    client = make_client(fake, tmp_home, me="member")
    html = client.get("/settings").text
    assert "Пробный доступ" in html and "лаб: 1 из 3" in html


def _llm_error(status, message):
    import httpx

    def handler(req):
        return httpx.Response(status, json={"error": {"message": message}})

    from forlabs.core.settings import Settings

    llm = LLM(Settings(mode="cloud"), "k", http_client=httpx.Client(transport=httpx.MockTransport(handler)),
              base_url="https://x/functions/v1/llm", retry_delays=())
    try:
        llm.chat([{"role": "user", "content": "hi"}])
    except LLMError as e:
        return e
    raise AssertionError("ожидалась ошибка")


def test_trial_limits_have_clear_messages():
    e = _llm_error(403, "trial labs limit: 3")
    assert "Пробный доступ" in e.message_ru and "3" in e.message_ru and "поддержк" in e.message_ru
    e = _llm_error(403, "trial chat limit: 5")
    assert "5" in e.message_ru and "правок" in e.message_ru and "поддержк" in e.message_ru
    assert "поддержк" in _llm_error(403, "trial lab calls limit").message_ru
    assert "Обнови ForLabs" in _llm_error(403, "trial needs lab tag").message_ru
    assert not _llm_error(403, "trial chat limit: 5").retryable


def test_trial_before_server_migration_gives_clear_message(admin, fake):
    fake.no_trial_columns = True
    r = admin.post(f"/admin/u/{M}/access", data={"action": "trial", "labs": "3", "chat": "5", "days": "7"},
                   follow_redirects=False)
    from urllib.parse import unquote

    assert r.status_code == 303 and "0005_trial.sql" in unquote(r.headers["location"])



def test_trial_usage_counts_labs_and_chats():
    from forlabs.cloud.store import TrialRow, TrialUsage

    u = TrialUsage(labs_limit=3, chat_limit=5, rows=[TrialRow(lab="p/s/1", calls=9, chats=2), TrialRow(lab="", calls=4)])
    assert u.labs_used == 1 and u.labs_left == 2
    assert u.chats_used("p/s/1") == 2 and u.chats_used("p/s/9") == 0


# ревью I7: без сети страницы у студента на пробном не висят на повторах запроса расхода
def test_trial_usage_offline_single_attempt_and_cached(fake, tmp_home):
    import time

    import httpx

    from forlabs.cloud.auth import CloudAuth
    from forlabs.cloud.store import CloudStore, Member
    from test_cloud import CFG, UID

    auth = CloudAuth(CFG, http=httpx.Client(transport=httpx.MockTransport(fake)), use_keyring=False)
    auth.sign_in("a@b.c", "верный")
    store = CloudStore(auth)
    m = Member(user_id=UID, trial_labs=3, trial_chat=5)
    fake.trial = [{"lab": "p/s/1", "calls": 3, "chats": 1, "has_chat": False}]
    assert store.trial_usage(m).labs_used == 1
    calls = len([c for c in fake.rpc_calls if c[0] == "trial_usage"])
    assert store.trial_usage(m).labs_used == 1  # из кэша
    assert len([c for c in fake.rpc_calls if c[0] == "trial_usage"]) == calls
    store._trial_cache.clear()
    fake.drop_next = 10
    t = time.monotonic()
    assert store.trial_usage(m) is None
    assert time.monotonic() - t < 0.5 and fake.drop_next == 9  # одна попытка, без пауз


def test_settings_check_for_trial_user_is_not_update_prompt(fake, tmp_home, monkeypatch):
    from forlabs.core.llm import LLMError

    client = make_client(fake, tmp_home, me="member")
    ctx = client.app.state.ctx

    class L:
        def check(self):
            raise LLMError("access", "trial needs lab tag", "Обнови ForLabs до последней версии")

    monkeypatch.setattr(ctx, "llm", lambda: L())
    r = client.post("/settings/check", follow_redirects=False)
    from urllib.parse import unquote

    loc = unquote(r.headers["location"])
    assert "msg=" in loc and "пробн" in loc and "Обнови" not in loc
