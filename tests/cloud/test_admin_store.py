"""Методы администратора в облачном хранилище (против фейкового Supabase)."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from test_cloud import CFG, UID, FakeSupabase

from forlabs.cloud.auth import CloudAuth
from forlabs.cloud.store import CloudStore, Member

M = "99999999-8888-7777-6666-555555555555"


def iso(dt):
    return dt.isoformat()


@pytest.fixture
def fake():
    f = FakeSupabase()
    f.tables["members"].append({"user_id": M, "email": "anna@example.com", "display_name": "Анна", "role": "member",
                                "approved": True, "access_until": None, "note": "", "allow_shared_key": True,
                                "last_seen_at": "2026-11-07T18:00:00+00:00", "created_at": "2026-11-01T09:00:00+00:00"})
    return f


@pytest.fixture
def store(fake, tmp_home):
    auth = CloudAuth(CFG, http=httpx.Client(transport=httpx.MockTransport(fake)), use_keyring=False)
    auth.sign_in("a@b.c", "верный")
    return CloudStore(auth)


def member(**kw):
    return Member(user_id=M, approved=True, **kw)


def test_member_access_state():
    now = datetime.now(timezone.utc)
    assert Member(user_id=M, approved=False).access_state == "pending"
    assert member(role="admin", access_until=iso(now - timedelta(days=5))).access_state == "unlimited"
    assert member().access_state == "unlimited" and member().days_left is None
    assert member(access_until=iso(now - timedelta(hours=1))).access_state == "expired"
    m = member(access_until=iso(now + timedelta(days=2, hours=1)))
    assert m.access_state == "expiring" and m.days_left == 3
    assert member(access_until=iso(now + timedelta(days=10))).access_state == "active"


def test_admin_overview(store, fake):
    fake.keys[M] = "5678"
    fake.daily = [{"day": "2026-11-06", "user_id": M, "tokens": 27000},
                  {"day": "2026-11-07", "user_id": M, "tokens": 3000},
                  {"day": "2026-11-07", "user_id": UID, "tokens": 500}]
    fake.tables["labs"] = [{"owner": M, "profile_id": "anna", "subject_id": "ssp", "number": "4", "status": "final",
                            "created_at": "2026-11-06T10:00:00+00:00", "updated_at": "2026-11-06T11:00:00+00:00"}]
    fake.tables["student_profiles"] = [{"owner": M, "id": "anna", "data": {
        "id": "anna", "full_name": "Иванова Анна Петровна", "title_name": "А.П. Иванова", "group": "РС02-23"}}]
    ov = store.admin_overview()
    assert [m.user_id for m in ov.members] == [UID, M]
    assert ov.keys[M].last4 == "5678" and UID not in ov.keys
    assert ov.usage_30d == {M: 30000, UID: 500}
    assert ov.labs[0].owner == M and ov.labs[0].status == "final"
    assert ov.profiles[M][0].full_name == "Иванова Анна Петровна"


def test_admin_member_card(store, fake):
    fake.keys[M] = "5678"
    fake.by_lab = [{"lab": "anna/ssp/4", "tokens": 27000, "calls": 9, "last_at": "2026-11-06T11:00:00+00:00"}]
    fake.daily = [{"day": "2026-11-06", "user_id": M, "tokens": 27000}, {"day": "2026-11-06", "user_id": UID, "tokens": 5}]
    fake.tables["member_events"] = [{"id": 1, "user_id": M, "actor": UID, "kind": "access",
                                     "detail": {"until": "2026-12-01T00:00:00+00:00"}, "created_at": "2026-11-05T10:00:00+00:00"}]
    card = store.admin_member(M)
    assert card.member.display_name == "Анна" and card.key.last4 == "5678"
    assert card.usage_by_lab["anna/ssp/4"] == (27000, 9)
    assert card.daily == [("2026-11-06", 27000)]
    assert card.events[0].kind == "access"


def test_admin_extend_from_future_date(store, fake):
    now = datetime.now(timezone.utc)
    fake.tables["members"][1]["access_until"] = iso(now + timedelta(days=10))
    store.admin_extend(M, 30)
    until = datetime.fromisoformat(fake.tables["members"][1]["access_until"])
    assert abs((until - now) - timedelta(days=40)) < timedelta(minutes=1)
    fake.tables["members"][1]["access_until"] = iso(now - timedelta(days=100))  # давно истекла — считаем от сегодня
    store.admin_extend(M, 7)
    until = datetime.fromisoformat(fake.tables["members"][1]["access_until"])
    assert abs((until - now) - timedelta(days=7)) < timedelta(minutes=1)


def test_admin_set_until_revoke_unlimited(store, fake):
    store.admin_set_until(M, "2026-12-12")
    assert fake.tables["members"][1]["access_until"] == "2026-12-12T23:59:59+03:00"
    store.admin_set_until(M, None)
    assert fake.tables["members"][1]["access_until"] is None
    store.admin_revoke(M)
    assert datetime.fromisoformat(fake.tables["members"][1]["access_until"]) <= datetime.now(timezone.utc)


def test_admin_update_member_only_allowed_fields(store, fake):
    store.admin_update_member(M, note="оплатил за ноябрь", display_name="Анна И.", role="admin")
    row = fake.tables["members"][1]
    assert row["note"] == "оплатил за ноябрь" and row["display_name"] == "Анна И." and row["role"] == "member"


def test_admin_set_key_validates(store, fake):
    with pytest.raises(ValueError):
        store.admin_set_key(M, "  abc ")
    with pytest.raises(ValueError):
        store.admin_set_key(M, "sk-test 1234 5678")
    assert fake.rpc_calls == []
    assert store.admin_set_key(M, " sk-test-12345678 ") == "5678"
    store.admin_clear_key(M)
    assert fake.keys == {}


def test_admin_balances_partial(store, fake):
    fake.balance = {M: 92011485, "shared": 5000}
    got = store.balances([M, UID], shared=True)
    assert got == {M: 92011485, UID: None, "shared": 5000}
    fake.functions_down = True
    assert store.balances([M]) == {}


def test_my_balance(store, fake):
    fake.balance = {UID: 1500}
    assert store.my_balance() == 1500


def test_download_member_report(store, fake, tmp_path):
    from forlabs.cloud.store import storage_key

    fake.objects[storage_key(M, "anna", "ssp", "4", "report.docx")] = b"PK-docx"
    out = tmp_path / "r.docx"
    assert store.download_member_report(M, "anna", "ssp", "4", out) is True
    assert out.read_bytes() == b"PK-docx"
    assert store.download_member_report(M, "anna", "ssp", "5", tmp_path / "x.docx") is False


def test_own_data_only_even_for_admin(store, fake):
    """Администратор читает чужие строки (для панели), но обычная работа приложения — только со своими."""
    from forlabs.core.models import LabState, Profile

    store.save_profile(Profile(id="ivanov", full_name="Иванов Иван", title_name="И.И. Иванов"))
    store.save_lab(LabState(profile_id="ivanov", subject_id="ssp", number="4", status="parsed"))
    fake.tables["student_profiles"].append({"owner": M, "id": "ivanov", "data": {
        "id": "ivanov", "full_name": "Чужой Иванов", "title_name": "Ч.И."}})
    fake.tables["labs"].insert(0, {"owner": M, "profile_id": "ivanov", "subject_id": "ssp", "number": "4",
                                   "status": "final", "state": {"profile_id": "ivanov", "subject_id": "ssp",
                                                                "number": "4", "status": "final"}})
    assert [p.full_name for p in store.list_profiles()] == ["Иванов Иван"]
    assert store.get_profile("ivanov").full_name == "Иванов Иван"
    assert store.load_lab("ivanov", "ssp", "4").status == "parsed"
    assert [s.status for s in store.list_labs("ivanov", "ssp")] == ["parsed"]
