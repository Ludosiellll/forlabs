import json
import time

import httpx
import pytest

from forlabs.cloud.auth import CloudAuth, CloudError
from forlabs.cloud.config import CloudConfig
from forlabs.cloud.store import CloudStore, storage_key
from forlabs.core.models import LabState, Profile

CFG = CloudConfig(url="https://proj.supabase.test", publishable_key="sb_publishable_x")
UID = "11111111-2222-3333-4444-555555555555"


class FakeSupabase:
    def __init__(self):
        self.tables = {"student_profiles": [], "labs": [], "members": [
            {"user_id": UID, "email": "a@b.c", "display_name": "Аня", "role": "admin", "approved": True}]}
        self.objects = {}
        self.requests = []
        self.keys = {}  # uid -> last4 (ключи участников; сами ключи сервер наружу не отдаёт)
        self.balance = {}  # uid|"shared" -> остаток у провайдера
        self.daily = []  # строки admin_usage_daily
        self.by_lab = []  # строки admin_usage_by_lab
        self.rpc_calls = []
        self.functions_down = False

    def session(self, expires_in=3600):
        return {"access_token": f"tok{len(self.requests)}", "refresh_token": "ref", "expires_in": expires_in,
                "expires_at": int(time.time()) + expires_in, "user": {"id": UID, "email": "a@b.c"}}

    def __call__(self, req: httpx.Request):
        self.requests.append(req)
        assert req.headers["apikey"] == "sb_publishable_x"
        path = req.url.path
        if path == "/auth/v1/token":
            body = json.loads(req.content)
            if req.url.params["grant_type"] == "password" and body["password"] != "верный":
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Invalid login credentials"})
            return httpx.Response(200, json=self.session())
        if path == "/auth/v1/signup":
            return httpx.Response(200, json={"id": UID, "email": "a@b.c", "confirmation_sent_at": "now"})
        assert req.headers["authorization"].startswith("Bearer tok")
        if path.startswith("/rest/v1/rpc/"):
            fn = path.rsplit("/", 1)[-1]
            args = json.loads(req.content or b"{}")
            self.rpc_calls.append((fn, args))
            if fn == "admin_set_key":
                k = args["api_key"].strip()
                if len(k) < 8:
                    return httpx.Response(400, json={"code": "22023", "message": "key too short"})
                self.keys[args["target"]] = k[-4:]
                return httpx.Response(200, json=k[-4:])
            if fn == "admin_clear_key":
                self.keys.pop(args["target"], None)
                return httpx.Response(204)
            if fn == "admin_keys":
                return httpx.Response(200, json=[{"user_id": u, "last4": v, "set_at": "2026-11-01T10:00:00+00:00"}
                                                 for u, v in self.keys.items()])
            if fn == "admin_usage_daily":
                return httpx.Response(200, json=self.daily)
            if fn == "admin_usage_by_lab":
                return httpx.Response(200, json=self.by_lab)
            return httpx.Response(404, json={"message": f"нет функции {fn}"})
        if path == "/functions/v1/llm/balance":
            if self.functions_down:
                raise httpx.ConnectError("нет связи")
            body = json.loads(req.content or b"{}")
            ids = body.get("user_ids") or [UID]
            out = {i: self.balance.get(i) for i in ids}
            if body.get("shared"):
                out["shared"] = self.balance.get("shared")
            return httpx.Response(200, json={"balances": out, "errors": {}})
        if path.startswith("/rest/v1/"):
            table = path.split("/")[-1]
            rows = self.tables.setdefault(table, [])
            if req.method == "POST":
                for row in json.loads(req.content) if req.content.startswith(b"[") else [json.loads(req.content)]:
                    if table in ("student_profiles", "labs", "subject_memory", "rule_overrides", "custom_subjects"):
                        row.setdefault("owner", UID)  # в базе owner = auth.uid() по умолчанию
                    keys = {"student_profiles": ("id",), "labs": ("profile_id", "subject_id", "number")}.get(table, ())
                    rows[:] = [r for r in rows if any(r.get(k) != row.get(k) for k in keys)] if keys else rows
                    rows.append(row)
                return httpx.Response(201, json=[])
            if req.method == "GET":
                out = rows
                for k, v in req.url.params.items():
                    if v.startswith("eq."):
                        out = [r for r in out if str(r.get(k)) == v[3:]]
                return httpx.Response(200, json=out)
            if req.method == "PATCH":
                for k, v in req.url.params.items():
                    if v.startswith("eq."):
                        for r in rows:
                            if str(r.get(k)) == v[3:]:
                                r.update(json.loads(req.content))
                return httpx.Response(204)
            if req.method == "DELETE":
                return httpx.Response(204)
        if path.startswith("/storage/v1/object/labs/"):
            key = path[len("/storage/v1/object/labs/"):]
            if req.method == "POST":
                self.objects[key] = req.content
                return httpx.Response(200, json={"Key": key})
            if req.method == "GET":
                return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404)
        return httpx.Response(404, json={"message": "нет такого пути"})


@pytest.fixture
def fake():
    return FakeSupabase()


@pytest.fixture
def auth(fake, tmp_home):
    return CloudAuth(CFG, http=httpx.Client(transport=httpx.MockTransport(fake)), use_keyring=False)


def test_sign_in_wrong_password(auth):
    with pytest.raises(CloudError) as e:
        auth.sign_in("a@b.c", "не тот")
    assert "пароль" in e.value.message_ru


def test_sign_in_and_refresh(auth, fake):
    s = auth.sign_in("a@b.c", "верный")
    assert s.user_id == UID and auth.access_token() == s.access_token
    auth.session.expires_at = int(time.time()) + 10  # скоро истечёт
    new = auth.access_token()
    assert new != s.access_token
    assert any(r.url.params.get("grant_type") == "refresh_token" for r in fake.requests)


def test_session_persists(auth, fake, tmp_home):
    auth.sign_in("a@b.c", "верный")
    again = CloudAuth(CFG, http=httpx.Client(transport=httpx.MockTransport(fake)), use_keyring=False)
    assert again.load_session() is not None and again.session.user_id == UID


def test_store_profiles_and_labs(auth, tmp_home):
    auth.sign_in("a@b.c", "верный")
    store = CloudStore(auth)
    p = Profile(id="ivanov", full_name="Иванов Иван", title_name="И.И. Иванов")
    store.save_profile(p)
    assert [x.id for x in store.list_profiles()] == ["ivanov"]
    assert store.get_profile("ivanov") == p
    st = LabState(profile_id="ivanov", subject_id="ssp", number="5.2", status="parsed")
    store.save_lab(st)
    assert store.load_lab("ivanov", "ssp", "5.2").status == "parsed"
    assert [s.number for s in store.list_labs("ivanov", "ssp")] == ["5.2"]
    st.updated_at = "2000-01-01T00:00:00"
    store.save_lab(st)
    assert store.load_lab("ivanov", "ssp", "5.2").updated_at > "2026"  # время изменения обновляется


def test_files_roundtrip(auth, tmp_home, tmp_path):
    auth.sign_in("a@b.c", "верный")
    store = CloudStore(auth)
    f = tmp_path / "рисунок.png"
    f.write_bytes(b"PNG...")
    key = store.upload(f, "ivanov", "ssp", "5.2", "assets/рисунок.png")
    assert key.startswith(f"{UID}/ivanov/ssp/lr5_2/") and key.isascii()
    out = tmp_path / "back.png"
    store.download(key, out)
    assert out.read_bytes() == b"PNG..."


def test_fetch_by_lab_path(auth, tmp_home, tmp_path):
    auth.sign_in("a@b.c", "верный")
    store = CloudStore(auth)
    f = tmp_path / "схема.png"
    f.write_bytes(b"PNG1")
    store.upload(f, "ivanov", "ssp", "4", "assets/scheme_1_2.png")
    out = tmp_path / "other" / "assets" / "scheme_1_2.png"
    assert store.fetch("ivanov", "ssp", "4", "assets/scheme_1_2.png", out) is True
    assert out.read_bytes() == b"PNG1"
    assert store.fetch("ivanov", "ssp", "4", "assets/нет.png", tmp_path / "x.png") is False


def test_custom_subjects_roundtrip(auth, tmp_home):
    from forlabs.core.subjects import Subject

    auth.sign_in("a@b.c", "верный")
    store = CloudStore(auth)
    store.save_custom_subject(Subject(id="oop", name="Объектно-ориентированное программирование", short_name="ООП"))
    got = store.list_custom_subjects()
    assert [s.short_name for s in got] == ["ООП"]


def test_storage_key_is_ascii():
    assert storage_key(UID, "иванов", "ssp", "5.2", "assets/Рисунок 1.png").isascii()


def test_members_admin(auth):
    auth.sign_in("a@b.c", "верный")
    store = CloudStore(auth)
    me = store.member()
    assert me.role == "admin" and me.approved
    store.set_approved(UID, False)
    assert store.list_members()[0].approved is False
