"""Переходы между страницами не ждут облако: ответы чтения отдаются из памяти, а свежие подтягиваются в фоне.
Своя запись сбрасывает память, состояние открытой лабы всегда читается заново (с ним работают правки)."""

import threading
import time

import httpx
import pytest
from test_cloud import CFG, UID, FakeSupabase

from forlabs.cloud.auth import CloudAuth
from forlabs.cloud.store import CloudStore
from forlabs.core.models import Enrollment, LabState, Profile


@pytest.fixture
def fake():
    return FakeSupabase()


@pytest.fixture
def store(fake, tmp_home):
    auth = CloudAuth(CFG, http=httpx.Client(transport=httpx.MockTransport(fake)), use_keyring=False)
    auth.sign_in("a@b.c", "верный")
    s = CloudStore(auth)
    s.save_profile(Profile(id="ivanov", full_name="Иванов Иван", title_name="И.И. Иванов", enrollments=[
        *(Enrollment(subject_id=s, title_subject=s, teacher="Т.Т. Тестова", academic_year="2026/2027") for s in ("ssp", "psip", "web"))]))
    for subject, number in (("ssp", "1"), ("ssp", "2"), ("psip", "7")):
        s.save_lab(LabState(profile_id="ivanov", subject_id=subject, number=number, status="parsed"))
    return s


def calls(fake, since: int, part: str = "") -> list[str]:
    return [f"{r.method} {r.url.path}" for r in fake.requests[since:] if part in r.url.path]


def settle(store) -> None:
    """Дождаться фоновых обновлений."""
    end = time.time() + 5
    while store._refreshing and time.time() < end:
        time.sleep(0.01)


def test_second_visit_reads_nothing_from_the_cloud(store, fake):
    store.list_profiles()
    store.list_lab_summaries("ivanov", "ssp")
    store.list_custom_subjects()
    n = len(fake.requests)
    assert [p.id for p in store.list_profiles()] == ["ivanov"]
    assert [x.number for x in store.list_lab_summaries("ivanov", "ssp")] == ["1", "2"]
    store.list_custom_subjects()
    assert calls(fake, n) == []


def test_all_subjects_come_in_one_request(store, fake):
    n = len(fake.requests)
    counts = {s: len(store.list_lab_summaries("ivanov", s)) for s in ("ssp", "psip", "web")}
    assert counts == {"ssp": 2, "psip": 1, "web": 0}
    assert len(calls(fake, n, "/labs")) == 1  # не запрос на каждый предмет


def test_own_changes_are_seen_at_once(store, fake):
    store.list_profiles()
    store.list_lab_summaries("ivanov", "ssp")
    store.save_profile(Profile(id="petrov", full_name="Петров Пётр", title_name="П.П. Петров"))
    store.save_lab(LabState(profile_id="ivanov", subject_id="ssp", number="3", status="rendered"))
    assert [p.id for p in store.list_profiles()] == ["ivanov", "petrov"]
    labs = store.list_lab_summaries("ivanov", "ssp")
    assert [x.number for x in labs] == ["1", "2", "3"] and labs[-1].status == "rendered"
    store.delete_lab("ivanov", "ssp", "1")
    assert [x.number for x in store.list_lab_summaries("ivanov", "ssp")] == ["2", "3"]


def test_saving_a_lab_does_not_make_the_list_ask_the_cloud_again(store, fake):
    store.list_lab_summaries("ivanov", "ssp")
    st = store.load_lab("ivanov", "ssp", "2", strict=True)
    st.status = "final"
    n = len(fake.requests)
    store.save_lab(st)  # идёт выполнение лабы: состояние пишется на каждом шаге
    labs = store.list_lab_summaries("ivanov", "ssp")
    assert [x.status for x in labs if x.number == "2"] == ["final"]
    assert [c for c in calls(fake, n, "/labs") if c.startswith("GET")] == []  # список обновлён на месте


def test_old_answer_is_shown_at_once_and_refreshed_behind(store, fake, monkeypatch):
    assert [p.id for p in store.list_profiles()] == ["ivanov"]
    fake.tables["student_profiles"].append({"owner": UID, "id": "sidorov", "data": Profile(
        id="sidorov", full_name="Сидоров Сидор", title_name="С.С. Сидоров").model_dump(mode="json")})  # другой компьютер
    monkeypatch.setattr(store, "FRESH", 0.0)  # ответ уже не свежий
    assert [p.id for p in store.list_profiles()] == ["ivanov"]  # сразу, без ожидания сети
    settle(store)
    assert [p.id for p in store.list_profiles()] == ["ivanov", "sidorov"]  # а следом — свежий


def test_open_lab_is_always_read_fresh(store, fake):
    store.load_lab("ivanov", "ssp", "1")
    n = len(fake.requests)
    store.load_lab("ivanov", "ssp", "1")
    assert len(calls(fake, n, "/labs")) == 1  # с состоянием лабы работают правки: только свежее


def test_no_connection_falls_back_to_the_copy_on_this_computer(store, fake):
    assert [p.id for p in store.list_profiles()] == ["ivanov"]
    store._forget()
    fake.drop_next = 5
    assert [p.id for p in store.list_profiles()] == ["ivanov"] and store.offline


def test_member_is_not_awaited_on_every_page(store, fake, monkeypatch):
    assert store.member().role == "admin"
    monkeypatch.setattr(store, "MEMBER_TTL", 0.0)
    n = len(fake.requests)
    t0 = time.perf_counter()
    assert store.member().role == "admin"  # прежнее значение сразу, свежее — в фоне
    assert time.perf_counter() - t0 < 0.5
    settle(store)
    assert len(calls(fake, n, "/members")) == 1


def test_pending_member_is_checked_every_time(store, fake):
    fake.tables["members"][0]["approved"] = False
    store._forget()
    store._member_cache = None
    assert store.member().approved is False
    fake.tables["members"][0]["approved"] = True  # администратор одобрил
    assert store.member().approved is True  # видно сразу, без ожидания


def test_admin_overview_asks_in_parallel(fake, tmp_home):
    lock, state = threading.Lock(), {"now": 0, "peak": 0}

    def slow(req):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.05)
        try:
            return fake(req)
        finally:
            with lock:
                state["now"] -= 1

    auth = CloudAuth(CFG, http=httpx.Client(transport=httpx.MockTransport(slow)), use_keyring=False)
    auth.sign_in("a@b.c", "верный")
    fake.tables["members"][0].update({"created_at": "2026-10-01T09:00:00+00:00"})
    t0 = time.perf_counter()
    overview = CloudStore(auth).admin_overview()
    assert overview.members and state["peak"] >= 3  # запросы идут вместе, а не один за другим
    assert time.perf_counter() - t0 < 0.25
