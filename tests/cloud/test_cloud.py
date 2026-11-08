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


def _field(row: dict, key: str):
    """Поле строки, в том числе путь внутрь JSON: state->>rev."""
    if "->>" in key:
        col, _, sub_key = key.partition("->>")
        v = (row.get(col) or {}).get(sub_key)
        return None if v is None else str(v)
    v = row.get(key)
    if isinstance(v, bool):  # PostgREST: eq.true / eq.false
        return "true" if v else "false"
    return None if v is None else str(v)


def _match(row: dict, params) -> bool:
    for k, v in params.items():
        if v.startswith("eq.") and _field(row, k) != v[3:]:
            return False
        if v == "is.null" and _field(row, k) is not None:
            return False
    return True


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
        self.balance_errors = {}  # uid -> текст ошибки, как в ответе функции
        self.drop_next = 0  # сколько следующих запросов к REST оборвать (как пропавший Wi-Fi)
        self.drop_auth_next = 0  # то же для обновления входа
        self.expired = set()  # токены, которые сервер уже считает просроченными (часы ноутбука спешат)
        self.trial = []  # строки trial_usage (расход пробного периода по лабам)
        self.no_trial_columns = False  # база без миграции 0005
        self.support_bound = False  # владелец привязал Telegram
        self.support_secrets = True  # секреты бота заданы в Supabase

    def session(self, expires_in=3600):
        return {"access_token": f"tok{len(self.requests)}", "refresh_token": "ref", "expires_in": expires_in,
                "expires_at": int(time.time()) + expires_in, "user": {"id": UID, "email": "a@b.c"}}

    def __call__(self, req: httpx.Request):
        self.requests.append(req)
        assert req.headers["apikey"] == "sb_publishable_x"
        path = req.url.path
        if path == "/auth/v1/token":
            if self.drop_auth_next:
                self.drop_auth_next -= 1
                raise httpx.ConnectError("Wi-Fi пропал")
            body = json.loads(req.content)
            if req.url.params["grant_type"] == "password" and body["password"] != "верный":
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Invalid login credentials"})
            return httpx.Response(200, json=self.session())
        if path == "/auth/v1/signup":
            return httpx.Response(200, json={"id": UID, "email": "a@b.c", "confirmation_sent_at": "now"})
        assert req.headers["authorization"].startswith("Bearer tok")
        if req.headers["authorization"][7:] in self.expired and path.startswith("/rest/v1/"):
            return httpx.Response(401, json={"code": "PGRST301", "message": "JWT expired"})
        if self.drop_next and path.startswith("/rest/v1/"):
            self.drop_next -= 1
            raise httpx.ConnectError("Wi-Fi пропал")
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
            if fn == "support_mark_read":
                for r in self.tables.get("support_messages", []):
                    if r["user_id"] == UID and r["from_admin"]:
                        r["read_at"] = r.get("read_at") or "2026-11-08T12:00:00+00:00"
                return httpx.Response(204)
            if fn == "admin_support_status":
                return httpx.Response(200, json=[{"telegram": self.support_bound, "bot_username": "forlabs_help_bot"}])
            if fn == "trial_usage":
                return httpx.Response(200, json=self.trial)
            if fn == "retag_my_usage":
                return httpx.Response(204)
            return httpx.Response(404, json={"message": f"нет функции {fn}"})
        if path == "/functions/v1/llm/balance":
            if self.functions_down:
                raise httpx.ConnectError("нет связи")
            body = json.loads(req.content or b"{}")
            ids = body.get("user_ids") or [UID]
            out = {i: self.balance.get(i) for i in ids}
            errors = {i: self.balance_errors[i] for i in ids if i in self.balance_errors}
            if body.get("shared"):
                out["shared"] = self.balance.get("shared")
            return httpx.Response(200, json={"balances": out, "errors": errors})
        if path == "/functions/v1/support/send":
            text = (json.loads(req.content or b"{}").get("text") or "").strip()
            if not text:
                return httpx.Response(400, json={"error": {"message": "empty message"}})
            rows = self.tables.setdefault("support_messages", [])
            if sum(1 for r in rows if r["user_id"] == UID and not r["from_admin"]) >= 20:
                return httpx.Response(429, json={"error": {"message": "support rate limit"}})
            rows.append({"id": len(rows) + 1, "user_id": UID, "from_admin": False, "text": text[:4000],
                         "created_at": f"2026-11-08T12:{len(rows):02d}:00+00:00", "read_at": None})
            return httpx.Response(200, json={"ok": True, "id": len(rows), "telegram": self.support_bound})
        if path == "/functions/v1/support/setup":
            if not self.support_secrets:
                return httpx.Response(503, json={"error": {"message": "telegram secrets missing"}})
            return httpx.Response(200, json={"bot": "forlabs_help_bot", "link": "https://t.me/forlabs_help_bot?start=Ab12Cd34",
                                             "bound": self.support_bound})
        if path.startswith("/rest/v1/"):
            table = path.split("/")[-1]
            rows = self.tables.setdefault(table, [])
            if req.method == "POST":
                for row in json.loads(req.content) if req.content.startswith(b"[") else [json.loads(req.content)]:
                    if table in ("student_profiles", "labs", "subject_memory", "rule_overrides", "custom_subjects"):
                        row.setdefault("owner", UID)  # в базе owner = auth.uid() по умолчанию
                    keys = {"student_profiles": ("id",), "labs": ("profile_id", "subject_id", "number"),
                            "member_notes": ("user_id",)}.get(table, ())
                    same = [r for r in rows if keys and all(r.get(k) == row.get(k) for k in keys)]
                    if same and "merge-duplicates" not in req.headers.get("prefer", ""):
                        return httpx.Response(409, json={"code": "23505", "message": "duplicate key"})
                    rows[:] = [r for r in rows if any(r.get(k) != row.get(k) for k in keys)] if keys else rows
                    if table == "support_messages":
                        row.setdefault("id", len(rows) + 1)
                        row.setdefault("created_at", f"2026-11-08T13:{len(rows):02d}:00+00:00")
                        row.setdefault("read_at", None)
                    rows.append(row)
                return httpx.Response(201, json=[])
            if req.method == "GET":
                return httpx.Response(200, json=[r for r in rows if _match(r, req.url.params)])
            if req.method == "PATCH":
                if self.no_trial_columns and any(k.startswith("trial") for k in json.loads(req.content)):
                    return httpx.Response(400, json={"code": "PGRST204", "message":
                                                     "Could not find the 'trial_chat' column of 'members' in the schema cache"})
                hit = [r for r in rows if _match(r, req.url.params)]
                for r in hit:
                    r.update(json.loads(req.content))
                if "return=representation" in req.headers.get("prefer", ""):
                    return httpx.Response(200, json=hit)
                return httpx.Response(204)
            if req.method == "DELETE":
                conds = {k: v[3:] for k, v in req.url.params.items() if v.startswith("eq.")}
                rows[:] = [r for r in rows if any(str(r.get(k)) != v for k, v in conds.items())] if conds else rows
                return httpx.Response(204)
        if path == "/storage/v1/object/list/labs":
            body = json.loads(req.content)
            prefix = body["prefix"].rstrip("/") + "/"
            names = set()
            for key in self.objects:
                if key.startswith(prefix):
                    rest = key[len(prefix):]
                    head = rest.split("/", 1)[0]
                    names.add((head, "/" not in rest))
            return httpx.Response(200, json=[{"name": n, "id": "x" if is_file else None} for n, is_file in sorted(names)])
        if path == "/storage/v1/object/labs" and req.method == "DELETE":
            for key in json.loads(req.content)["prefixes"]:
                self.objects.pop(key, None)
            return httpx.Response(200, json=[])
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


def test_rest_retries_a_dropped_connection(auth, fake):
    auth.sign_in("a@b.c", "верный")
    store = CloudStore(auth, retry_delays=(0, 0))
    fake.drop_next = 2  # два обрыва подряд — третий запрос проходит
    assert store.list_profiles() == []
    fake.drop_next = 5
    with pytest.raises(CloudError):
        store.list_members()  # у этого запроса нет копии «без связи» — после повторов ошибка


def test_delete_lab_removes_cache_and_cloud_files(auth, fake, tmp_home, tmp_path):
    from forlabs.core.llm import FakeLLM
    from forlabs.pipeline.run import Pipeline

    auth.sign_in("a@b.c", "верный")
    store = CloudStore(auth, retry_delays=(0, 0))
    store.save_profile(Profile(id="ivanov", full_name="Иванов Иван", title_name="И.И. Иванов"))
    pipe = Pipeline(store, llm_factory=lambda: FakeLLM([]), prefer_word=False, render_diagrams=False)
    st = pipe.create_lab("ivanov", "ssp", [("старая_методичка_ЛР5.txt", b"old")], number="5")
    asset = pipe.lab_dir(st) / "assets" / "fig.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"PNG")
    store.upload(asset, "ivanov", "ssp", "5", "assets/fig.png")
    store.upload(asset, "ivanov", "ssp", "5", "report.docx")
    store.delete_lab("ivanov", "ssp", "5")
    assert store.load_lab("ivanov", "ssp", "5") is None
    assert not any("/lr5/" in k for k in fake.objects)  # файлы лабы в облаке удалены
    st = pipe.create_lab("ivanov", "ssp", [("новая.txt", b"new")], number="5")
    assert [f.name for f in (pipe.lab_dir(st) / "source").iterdir()] == ["новая.txt"]


def test_offline_shows_local_copy(auth, fake, tmp_home):
    auth.sign_in("a@b.c", "верный")
    store = CloudStore(auth, retry_delays=(0, 0))
    store.save_profile(Profile(id="ivanov", full_name="Иванов Иван", title_name="И.И. Иванов"))
    store.save_lab(LabState(profile_id="ivanov", subject_id="ssp", number="4", status="rendered"))
    assert [p.id for p in store.list_profiles()] == ["ivanov"]  # заодно кэш профилей
    fake.drop_next = 10_000  # интернет пропал
    assert store.load_lab("ivanov", "ssp", "4").status == "rendered"
    assert [s.number for s in store.list_labs("ivanov", "ssp")] == ["4"]
    assert store.get_profile("ivanov").full_name == "Иванов Иван"
    assert [p.id for p in store.list_profiles()] == ["ivanov"]
    assert store.offline is True
    fake.drop_next = 0
    store._offline_until = 0  # прошло 30 с — снова пробуем облако
    store.list_profiles()
    assert store.offline is False
