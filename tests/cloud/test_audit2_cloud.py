"""Аудит-2, облако: два компьютера, без связи, сбитые часы, загрузки файлов."""

import time

import httpx
import pytest
from test_cloud import CFG, UID, FakeSupabase

from forlabs.cloud.auth import CloudAuth, CloudError
from forlabs.cloud.store import CloudStore
from forlabs.core.models import LabState, Profile


def make(tmp_path, fake=None, name="pc"):
    fake = fake or FakeSupabase()
    auth = CloudAuth(CFG, http=httpx.Client(transport=httpx.MockTransport(fake)), use_keyring=False)
    auth.sign_in("a@b.c", "верный")
    return fake, auth, CloudStore(auth, cache_home=tmp_path / name, retry_delays=(0, 0))


def two_pcs(tmp_path):
    fake, auth_a, a = make(tmp_path, name="a")
    auth_b = CloudAuth(CFG, http=httpx.Client(transport=httpx.MockTransport(fake)), use_keyring=False)
    auth_b.sign_in("a@b.c", "верный")
    b = CloudStore(auth_b, cache_home=tmp_path / "b", retry_delays=(0, 0))
    a.save_profile(Profile(id="ivan", full_name="Иванов Иван", title_name="И. Иванов"))
    return fake, a, b


# C1: решения пайплайна и записи не опираются на копию «без связи»
def test_strict_load_does_not_fall_back_to_cache(tmp_path):
    fake, a, b = two_pcs(tmp_path)
    a.save_lab(LabState(profile_id="ivan", subject_id="psip", number="3", status="final"))
    fake.drop_next = 10
    with pytest.raises(CloudError):
        b.load_lab("ivan", "psip", "3", strict=True)


def test_offline_copy_cannot_be_saved(tmp_path):
    fake, a, b = two_pcs(tmp_path)
    st = LabState(profile_id="ivan", subject_id="psip", number="3", status="rendered")
    a.save_lab(st)
    fake.drop_next = 10
    copy = a.load_lab("ivan", "psip", "3")  # копия с этого компьютера
    assert copy is not None and a.offline
    fake.drop_next = 0
    with pytest.raises(CloudError):
        a.save_lab(copy)


def test_offline_blip_during_create_does_not_overwrite(tmp_path):
    from forlabs.pipeline.run import Pipeline

    fake, a, b = two_pcs(tmp_path)
    a.save_lab(LabState(profile_id="ivan", subject_id="psip", number="3", status="final"))
    pipe = Pipeline(b, llm_factory=lambda: None, render_diagrams=False, prefer_word=False)
    fake.drop_next = 3
    with pytest.raises(CloudError):
        pipe.create_lab("ivan", "psip", [("m.txt", "ЛР 3".encode())], number="3")
    assert a.load_lab("ivan", "psip", "3").status == "final"


# C2: без связи страницы открываются сразу, а не через десятки секунд повторов
def test_offline_reads_are_fast(tmp_path, monkeypatch):
    fake, a, _ = two_pcs(tmp_path)
    a.retry_delays = (1.0, 3.0)
    slept = []
    monkeypatch.setattr("forlabs.cloud.store.time.sleep", slept.append)
    fake.drop_next = 1000
    before = len(fake.requests)
    a.list_profiles()
    a.list_lab_summaries("ivan", "psip")
    a.get_profile("ivan")
    assert slept == []  # читающие запросы с копией не ждут повторов
    assert len(fake.requests) - before <= 1  # после первого обрыва 30 с идём сразу в копию


# C3: часы ноутбука сбиты — срок входа считается от своих часов, а не от серверного времени
def test_session_expiry_uses_local_clock(tmp_path):
    fake = FakeSupabase()
    real = fake.session
    fake.session = lambda expires_in=3600: {**real(expires_in), "expires_at": int(time.time()) - 7200}
    _, auth, _ = make(tmp_path, fake)
    assert auth.session.expires_at > time.time() + 3000


def test_llm_refreshes_login_once_on_401(tmp_path):
    from forlabs.core.llm import LLM
    from forlabs.core.settings import Settings

    calls = []

    def handler(req):
        calls.append(req.headers["authorization"])
        if len(calls) == 1:
            return httpx.Response(401, json={"error": {"message": "not signed in"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "да"}, "finish_reason": "stop"}]})

    refreshed = []
    llm = LLM(Settings(), api_key=lambda: f"tok{len(refreshed)}",
              http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    llm.on_unauthorized = lambda: refreshed.append(1)
    assert llm.chat([{"role": "user", "content": "?"}]) == "да"
    assert refreshed == [1] and calls == ["Bearer tok0", "Bearer tok1"]


def test_login_refresh_network_blip_is_retryable():
    from forlabs.core.llm import LLM, LLMError
    from forlabs.core.settings import Settings

    def key():
        raise CloudError("Нет связи с облаком. Проверь интернет.", "обрыв", offline=True)

    llm = LLM(Settings(), api_key=key, http_client=httpx.Client(transport=httpx.MockTransport(lambda r: None)),
              retry_delays=())
    with pytest.raises(LLMError) as e:
        llm.chat([{"role": "user", "content": "?"}])
    assert e.value.retryable and e.value.kind == "network"


# C4: файл не ушёл в облако — повтор, потом список ожидающих и отправка при следующей вёрстке
def test_upload_retries_blips(tmp_path):
    fake, a, _ = two_pcs(tmp_path)
    f = tmp_path / "shot.png"
    f.write_bytes(b"png")
    real = fake.__call__
    drops = {"n": 2}

    def flaky(req):
        if req.url.path.startswith("/storage/v1/object/labs/") and req.method == "POST" and drops["n"]:
            drops["n"] -= 1
            raise httpx.ConnectError("Wi-Fi")
        return real(req)

    a.http = httpx.Client(transport=httpx.MockTransport(flaky))
    a.upload(f, "ivan", "psip", "3", "assets/shot.png")
    assert any(k.endswith("assets/shot.png") for k in fake.objects)


def test_failed_upload_is_retried_later(tmp_path, monkeypatch):
    from forlabs.pipeline.run import Pipeline

    fake, a, _ = two_pcs(tmp_path)
    pipe = Pipeline(a, llm_factory=lambda: None, render_diagrams=False, prefer_word=False)
    st = pipe.create_lab("ivan", "psip", [("m.txt", b"x")], number="3")
    f = pipe.lab_dir(st) / "assets" / "shot.png"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"png")
    monkeypatch.setattr(a, "upload", lambda *args, **kw: (_ for _ in ()).throw(CloudError("нет связи", offline=True)))
    pipe._sync(st, ["assets/shot.png"])
    assert st.pending_uploads == ["assets/shot.png"]
    monkeypatch.undo()
    pipe._retry_pending(st)
    assert st.pending_uploads == [] and any(k.endswith("assets/shot.png") for k in fake.objects)


# C5: два компьютера — изменение с другого не затирается молча
def test_conflicting_save_from_other_computer_is_refused(tmp_path):
    fake, a, b = two_pcs(tmp_path)
    a.save_lab(LabState(profile_id="ivan", subject_id="psip", number="3", status="rendered"))
    on_a = a.load_lab("ivan", "psip", "3")
    on_b = b.load_lab("ivan", "psip", "3")
    on_b.accepted_rules = ["принято на B"]
    b.save_lab(on_b)
    on_a.status = "final"
    with pytest.raises(CloudError) as e:
        a.save_lab(on_a)
    assert "другом компьютере" in e.value.message_ru
    assert a.load_lab("ivan", "psip", "3").accepted_rules == ["принято на B"]


def test_sequential_saves_and_renumber_work(tmp_path):
    fake, a, _ = two_pcs(tmp_path)
    st = LabState(profile_id="ivan", subject_id="psip", number="новая-1")
    for _ in range(3):
        a.save_lab(st)
    moved = st.model_copy(update={"number": "7"})
    a.save_lab(moved)  # под новым номером строки ещё нет — создаётся
    assert a.load_lab("ivan", "psip", "7") is not None


def test_retried_save_is_not_a_conflict(tmp_path):
    fake, a, _ = two_pcs(tmp_path)
    st = LabState(profile_id="ivan", subject_id="psip", number="3")
    a.save_lab(st)
    real = fake.__call__
    lost = {"n": 1}

    def lose_reply(req):  # запрос дошёл, ответ потерялся — повтор не должен стать «конфликтом»
        r = real(req)
        if req.method == "PATCH" and lost["n"]:
            lost["n"] -= 1
            raise httpx.ReadError("ответ потерялся")
        return r

    a.http = httpx.Client(transport=httpx.MockTransport(lose_reply))
    a.save_lab(st)


def test_running_elsewhere_is_visible(tmp_path, monkeypatch):
    from forlabs.pipeline import run

    fake, a, b = two_pcs(tmp_path)
    monkeypatch.setattr(run, "machine_id", lambda: "A")
    pipe = run.Pipeline(a, llm_factory=lambda: None, render_diagrams=False, prefer_word=False)
    st = LabState(profile_id="ivan", subject_id="psip", number="3", status="executing")
    pipe._save(st)
    seen = b.load_lab("ivan", "psip", "3")
    monkeypatch.setattr(run, "machine_id", lambda: "B")
    assert run.running_elsewhere(seen)
    st.status = "rendered"
    pipe._save(st)
    assert not run.running_elsewhere(b.load_lab("ivan", "psip", "3"))


# C7: копия на компьютере обновляется при чтении и пишется атомарно
def test_cache_updated_on_read(tmp_path):
    fake, a, b = two_pcs(tmp_path)
    a.save_lab(LabState(profile_id="ivan", subject_id="psip", number="3", status="final"))
    b.load_lab("ivan", "psip", "3")
    fake.drop_next = 100
    assert b.load_lab("ivan", "psip", "3").status == "final"


# C8: «Выйти и стереть копию» на общем компьютере
def test_wipe_local_copy(tmp_path):
    fake, a, _ = two_pcs(tmp_path)
    a.save_lab(LabState(profile_id="ivan", subject_id="psip", number="3"))
    assert any((tmp_path / "a").rglob("state.json"))
    a.wipe_local()
    assert not any((tmp_path / "a").rglob("state.json"))
    assert UID  # кэш именно этого пользователя
