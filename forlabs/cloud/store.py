"""Облачное хранилище: те же операции, что у локального Store, но данные в Supabase (RLS: только свои),
файлы — в приватном bucket. Локальная папка служит кэшем для файлов."""

from __future__ import annotations

import hashlib
import re
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from forlabs.cloud.admin import (
    AdminOverview,
    KeyInfo,
    LabRow,
    MemberCard,
    MemberEvent,
    access_state,
    days_left,
    end_of_day_minsk,
    now_utc,
    parse_ts,
)
from forlabs.cloud.auth import CloudAuth, CloudError
from forlabs.core import paths
from forlabs.core.fs import atomic_write_bytes, atomic_write_text
from forlabs.core.models import LabState, LabSummary, Profile, summarize
from forlabs.core.netcert import is_cert_error, offline_message
from forlabs.core.store import Store, _number_key, _read_model, safe_name

BUCKET = "labs"
OFFLINE_HOLD = 30.0  # секунд после обрыва чтение идёт сразу в копию на компьютере
CONFLICT_MESSAGE = ("Лаба изменилась на другом компьютере или в другой вкладке — обнови страницу. Изменения с того "
                    "компьютера сохранены, а это действие — нет, повтори его.")
OFFLINE_COPY_MESSAGE = ("Нет связи с облаком: лаба открыта из копии на этом компьютере — её можно смотреть и "
                        "скачивать, а менять — когда вернётся связь.")


class Member(BaseModel):
    user_id: str
    email: str = ""
    display_name: str = ""
    role: str = "member"
    approved: bool = False
    created_at: str | None = None
    access_until: str | None = None
    note: str = ""
    allow_shared_key: bool = True
    last_seen_at: str | None = None

    @property
    def access_state(self) -> str:
        return access_state(self.approved, self.role, self.access_until)

    @property
    def days_left(self) -> int | None:
        return None if self.role == "admin" else days_left(self.access_until)

    @property
    def name(self) -> str:
        return self.display_name or self.email.split("@")[0] or "Участник"


def _ascii(s: str) -> str:
    a = re.sub(r"[^0-9A-Za-z._-]+", "_", s).strip("_")
    if a and a == s:
        return a
    h = hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]
    return f"{a[:40]}_{h}" if a else h


def storage_key(uid: str, profile_id: str, subject_id: str, number: str, rel: str) -> str:
    parts = [_ascii(p) for p in Path(rel).as_posix().split("/") if p]
    return "/".join([uid, _ascii(profile_id), _ascii(subject_id), "lr" + _ascii(number.replace(".", "_"))] + parts)


class CloudStore:
    def __init__(self, auth: CloudAuth, cache_home: Path | None = None,
                 retry_delays: tuple[float, ...] = (1.0, 3.0)):
        self.auth = auth
        self.retry_delays = retry_delays
        self.http = auth.http
        self.cfg = auth.config
        self.local = Store(cache_home or paths.data_home() / "cloud_cache")
        self.offline = False
        self._offline_until = 0.0  # недавно не было связи: чтение с копией идёт сразу в копию

    # HTTP
    def _h(self, extra: dict | None = None) -> dict:
        h = {"apikey": self.cfg.publishable_key, "Authorization": f"Bearer {self.auth.access_token()}"}
        h.update(extra or {})
        return h

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code in (401, 403):
            raise CloudError("Нет доступа: войди заново или дождись одобрения администратора.", r.text[:200])
        if r.status_code >= 400:
            raise CloudError(f"Облако ответило ошибкой {r.status_code}.", r.text[:300])
        return r

    def _rest(self, method: str, table: str, params: dict | None = None, json=None, prefer: str | None = None,
              retries: bool = True, ok: tuple[int, ...] = ()):
        """Запрос к базе; обрыв связи и 5xx повторяются (все наши записи — upsert/patch/delete по ключу,
        повтор безопасен)."""
        refreshed = False
        attempt = 0
        delays = self.retry_delays if retries else ()
        while True:
            try:
                r = self.http.request(method, f"{self.cfg.url}/rest/v1/{table}", params=params, json=json,
                                      headers=self._h({"Prefer": prefer} if prefer else None))
            except CloudError as e:  # обрыв при обновлении входа — повторяется, как и сам запрос
                if not e.offline or attempt == len(delays) or is_cert_error(e.detail):
                    raise
                time.sleep(delays[attempt])
                attempt += 1
                continue
            except httpx.TransportError as e:
                if attempt == len(delays) or is_cert_error(str(e)):
                    raise CloudError(offline_message(e), str(e), offline=True) from e
                time.sleep(delays[attempt])
                attempt += 1
                continue
            if r.status_code >= 500 and attempt < len(delays):
                time.sleep(delays[attempt])
                attempt += 1
                continue
            if r.status_code == 401 and not refreshed:  # часы компьютера сбиты: сервер считает вход просроченным
                refreshed = True
                self.auth.refresh()
                continue
            self.offline, self._offline_until = False, 0.0
            if r.status_code in ok:
                return r
            return self._check(r)

    def _mine(self) -> dict:
        """Только свои строки: администратор по политикам видит и чужие, но работает со своими."""
        return {"owner": f"eq.{self.auth.user_id}"}

    def _upsert(self, table: str, row: dict, on_conflict: str) -> None:
        self._rest("POST", table, params={"on_conflict": on_conflict}, json=row,
                   prefer="resolution=merge-duplicates,return=minimal")

    # профили
    # без связи — копия с этого компьютера (только чтение); offline включает плашку в интерфейсе
    def _profiles_cache(self) -> Path:
        return self.local.home / "profiles" / safe_name(self.auth.user_id or "anon")

    def _cache_profile(self, p: Profile) -> None:
        try:
            atomic_write_text(self._profiles_cache() / f"{safe_name(p.id)}.json", p.model_dump_json(indent=2))
        except OSError:
            pass

    def _cached_profiles(self) -> list[Profile]:
        d = self._profiles_cache()
        found = [_read_model(Profile, f) for f in d.glob("*.json")] if d.exists() else []
        return sorted((p for p in found if p is not None), key=lambda p: p.full_name)

    def _offline_or_raise(self, e: CloudError) -> None:
        if not e.offline:
            raise e
        self.offline = True
        self._offline_until = time.monotonic() + OFFLINE_HOLD

    def _read(self, table: str, params: dict):
        """Чтение, у которого есть копия на компьютере: одна попытка без пауз; None — связи нет, брать копию."""
        if time.monotonic() < self._offline_until:
            self.offline = True
            return None
        try:
            return self._rest("GET", table, params=params, retries=False).json()
        except CloudError as e:
            self._offline_or_raise(e)
            return None

    def list_profiles(self) -> list[Profile]:
        rows = self._read("student_profiles", {"select": "data", **self._mine()})
        if rows is None:
            return self._cached_profiles()
        profiles = sorted((Profile.model_validate(r["data"]) for r in rows), key=lambda p: p.full_name)
        for p in profiles:
            self._cache_profile(p)
        return profiles

    def get_profile(self, pid: str) -> Profile | None:
        rows = self._read("student_profiles", {"select": "data", "id": f"eq.{pid}", **self._mine()})
        if rows is None:
            return next((p for p in self._cached_profiles() if p.id == pid), None)
        p = Profile.model_validate(rows[0]["data"]) if rows else None
        if p:
            self._cache_profile(p)
        return p

    def save_profile(self, p: Profile) -> None:
        self._upsert("student_profiles", {"id": p.id, "data": p.model_dump(mode="json")}, "owner,id")
        self._cache_profile(p)

    def delete_profile(self, pid: str) -> None:
        self._rest("DELETE", "student_profiles", params={"id": f"eq.{pid}"})

    # лабы
    def lab_dir(self, profile_id: str, subject_id: str, number: str) -> Path:
        return self.local.home / "labs" / safe_name(self.auth.user_id) / safe_name(profile_id) / \
            safe_name(subject_id) / f"ЛР{safe_name(number)}"

    def _cache_lab(self, st: LabState) -> None:
        try:
            atomic_write_text(self.lab_dir(st.profile_id, st.subject_id, st.number) / "state.json",
                              st.model_dump_json(indent=2))
        except OSError:
            pass

    def _cached_lab(self, profile_id: str, subject_id: str, number: str) -> LabState | None:
        st = _read_model(LabState, self.lab_dir(profile_id, subject_id, number) / "state.json")
        if st is not None:
            st._offline_copy = True  # только смотреть: запись такой копии затёрла бы облако
        return st

    def _cached_labs(self, profile_id: str, subject_id: str) -> list[LabState]:
        d = self.lab_dir(profile_id, subject_id, "x").parent
        states = [self._cached_lab(profile_id, subject_id, f.name[2:]) for f in d.iterdir()
                  if f.is_dir() and f.name.startswith("ЛР") and ".old-" not in f.name] if d.exists() else []
        return sorted((x for x in states if x is not None), key=lambda x: _number_key(x.number))

    def _lab_key(self, st: LabState) -> dict:
        return {**self._mine(), "profile_id": f"eq.{st.profile_id}", "subject_id": f"eq.{st.subject_id}",
                "number": f"eq.{st.number}"}

    def save_lab(self, st: LabState) -> None:
        """Запись с проверкой версии (rev): если лабу успели изменить на другом компьютере — отказ, а не затирание."""
        from datetime import datetime

        if getattr(st, "_offline_copy", False):
            raise CloudError(OFFLINE_COPY_MESSAGE, "копия без связи", offline=True)
        import uuid

        expected, prev_write = st.rev, st.write_id
        st.rev, st.updated_at = expected + 1, datetime.now().isoformat(timespec="seconds")
        st.write_id = uuid.uuid4().hex[:12]
        body = {"status": st.status, "state": st.model_dump(mode="json")}
        try:
            cond = {"state->>rev": f"eq.{expected}"} if expected else {"state->>rev": "is.null"}
            hit = self._rest("PATCH", "labs", params={**self._lab_key(st), **cond}, json=body,
                             prefer="return=representation").json()
            if not hit:
                rows = self._rest("GET", "labs", params={"select": "state", **self._lab_key(st)}).json()
                if not rows:
                    r = self._rest("POST", "labs", json={"profile_id": st.profile_id, "subject_id": st.subject_id,
                                                         "number": st.number, **body},
                                   prefer="return=minimal", ok=(409,))
                    if r.status_code == 409:
                        raise CloudError(CONFLICT_MESSAGE, "лаба создана на другом компьютере")
                elif (rows[0].get("state") or {}).get("write_id") != st.write_id:
                    raise CloudError(CONFLICT_MESSAGE, f"в облаке версия {(rows[0].get('state') or {}).get('rev')}, "
                                                       f"у нас {expected}")
                # иначе — это наша же запись, ответ на которую потерялся при обрыве
        except CloudError:
            st.rev, st.write_id = expected, prev_write
            raise
        self._cache_lab(st)

    def load_lab(self, profile_id: str, subject_id: str, number: str, strict: bool = False) -> LabState | None:
        """strict — для решений и действий: без связи ошибка, а не копия с этого компьютера."""
        params = {"select": "state", **self._mine(), "profile_id": f"eq.{profile_id}",
                  "subject_id": f"eq.{subject_id}", "number": f"eq.{number}"}
        rows = self._rest("GET", "labs", params=params).json() if strict else self._read("labs", params)
        if rows is None:
            return self._cached_lab(profile_id, subject_id, number)
        st = LabState.model_validate(rows[0]["state"]) if rows else None
        if st is not None:
            self._cache_lab(st)
        return st

    def list_labs(self, profile_id: str, subject_id: str) -> list[LabState]:
        rows = self._read("labs", {"select": "state", **self._mine(), "profile_id": f"eq.{profile_id}",
                                   "subject_id": f"eq.{subject_id}"})
        if rows is None:
            return self._cached_labs(profile_id, subject_id)
        return sorted((LabState.model_validate(r["state"]) for r in rows), key=lambda s: _number_key(s.number))

    def list_lab_summaries(self, profile_id: str, subject_id: str) -> list[LabSummary]:
        """Для главной: номер, статус, тема и дата — без полного состояния (отчёт, версии, методичка)."""
        rows = self._read("labs", {"select": "number,status,updated:state->>updated_at,topic:state->spec->>topic",
                                   **self._mine(), "profile_id": f"eq.{profile_id}", "subject_id": f"eq.{subject_id}"})
        if rows is None:
            return [summarize(x) for x in self._cached_labs(profile_id, subject_id)]
        out = []
        for r in rows:
            state = r.get("state") or {}  # фейк в тестах отдаёт строку целиком
            topic = r.get("topic") if "topic" in r else ((state.get("spec") or {}).get("topic"))
            out.append(LabSummary(profile_id=profile_id, subject_id=subject_id, number=r["number"],
                                  status=r.get("status") or state.get("status") or "draft",
                                  updated_at=r.get("updated") or state.get("updated_at") or "", topic=topic))
        return sorted(out, key=lambda x: _number_key(x.number))

    def delete_lab(self, profile_id: str, subject_id: str, number: str) -> None:
        """Строка в базе, файлы в облаке и локальная копия: новая лаба с тем же номером начинается с чистого листа."""
        import shutil

        self._rest("DELETE", "labs", params={**self._mine(), "profile_id": f"eq.{profile_id}",
                                             "subject_id": f"eq.{subject_id}", "number": f"eq.{number}"})
        shutil.rmtree(self.lab_dir(profile_id, subject_id, number), ignore_errors=True)
        try:  # файлы — по возможности: без связи останутся в облаке, но лаба уже удалена
            prefix = storage_key(self.auth.user_id, profile_id, subject_id, number, "x").rsplit("/", 1)[0]
            keys = self._storage_list(prefix)
            for i in range(0, len(keys), 100):
                self.http.request("DELETE", f"{self.cfg.url}/storage/v1/object/{BUCKET}",
                                  json={"prefixes": keys[i:i + 100]}, headers=self._h())
        except (httpx.TransportError, CloudError):
            pass

    def _storage_list(self, prefix: str, depth: int = 0) -> list[str]:
        """Все файлы под папкой хранилища (список Supabase не рекурсивный)."""
        r = self.http.post(f"{self.cfg.url}/storage/v1/object/list/{BUCKET}", headers=self._h(),
                           json={"prefix": prefix, "limit": 1000, "offset": 0})
        if r.status_code >= 400:
            return []
        out = []
        for item in r.json() or []:
            name = f"{prefix}/{item['name']}"
            if item.get("id"):
                out.append(name)
            elif depth < 4:
                out += self._storage_list(name, depth + 1)
        return out

    # память предмета и правила
    def read_memory(self, profile_id: str, subject_id: str) -> str:
        rows = self._rest("GET", "subject_memory", params={"select": "text", **self._mine(), "profile_id": f"eq.{profile_id}",
                                                           "subject_id": f"eq.{subject_id}"}).json()
        return rows[0]["text"] if rows else ""

    def write_memory(self, profile_id: str, subject_id: str, text: str) -> None:
        self._upsert("subject_memory", {"profile_id": profile_id, "subject_id": subject_id, "text": text},
                     "owner,profile_id,subject_id")

    def rule_overrides(self) -> dict[str, str]:
        rows = self._rest("GET", "rule_overrides", params={"select": "path,text", **self._mine()}).json()
        return {r["path"]: r["text"] for r in rows}

    def save_rule_override(self, path: str, text: str) -> None:
        self._upsert("rule_overrides", {"path": path, "text": text}, "owner,path")

    def reset_rule_override(self, path: str) -> None:
        self._rest("DELETE", "rule_overrides", params={"path": f"eq.{path}"})

    # свои предметы (общие для всех компьютеров аккаунта)
    def list_custom_subjects(self) -> list:
        from forlabs.core.subjects import Subject

        rows = self._rest("GET", "custom_subjects", params={"select": "id,data", **self._mine()}).json()
        return [Subject.model_validate(r["data"]) for r in rows]

    def save_custom_subject(self, subject) -> None:
        self._upsert("custom_subjects", {"id": subject.id, "data": subject.model_dump(mode="json")}, "owner,id")

    # файлы
    def _storage(self, method: str, path: str, headers: dict | None = None, **kw) -> httpx.Response:
        """Запрос к хранилищу с теми же повторами, что у базы: обрыв, 5xx, просроченный вход (401) — один раз."""
        refreshed, attempt = False, 0
        while True:
            try:
                r = self.http.request(method, f"{self.cfg.url}/storage/v1/{path}", headers=self._h(headers), **kw)
            except CloudError as e:  # обрыв при обновлении входа
                if not e.offline or attempt >= len(self.retry_delays) or is_cert_error(e.detail):
                    raise
            except httpx.TransportError as e:
                if attempt >= len(self.retry_delays) or is_cert_error(str(e)):
                    raise CloudError(offline_message(e), str(e), offline=True) from e
            else:
                if r.status_code == 401 and not refreshed:
                    refreshed = True
                    self.auth.refresh()
                    continue
                if not (r.status_code >= 500 and attempt < len(self.retry_delays)):
                    return r
            time.sleep(self.retry_delays[attempt])
            attempt += 1

    def upload(self, local: Path, profile_id: str, subject_id: str, number: str, rel: str) -> str:
        key = storage_key(self.auth.user_id, profile_id, subject_id, number, rel)
        data = Path(local).read_bytes()
        r = self._storage("POST", f"object/{BUCKET}/{quote(key)}", content=data,
                          headers={"x-upsert": "true", "Content-Type": "application/octet-stream"})
        self._check(r)
        return key

    def download(self, key: str, local: Path) -> Path:
        r = self._check(self._storage("GET", f"object/{BUCKET}/{quote(key)}"))
        atomic_write_bytes(local, r.content)
        return Path(local)

    def fetch(self, profile_id: str, subject_id: str, number: str, rel: str, local: Path) -> bool:
        """Скачивает файл лабы по её пути (assets/…). False — такого файла в облаке нет."""
        key = storage_key(self.auth.user_id, profile_id, subject_id, number, rel)
        r = self._storage("GET", f"object/{BUCKET}/{quote(key)}")
        if r.status_code == 404 or (r.status_code == 400 and "not_found" in r.text.replace(" ", "_").lower()):
            return False
        self._check(r)
        atomic_write_bytes(local, r.content)
        return True

    def delete_file(self, profile_id: str, subject_id: str, number: str, rel: str) -> None:
        key = storage_key(self.auth.user_id, profile_id, subject_id, number, rel)
        self._storage("DELETE", f"object/{BUCKET}", json={"prefixes": [key]})

    def wipe_local(self) -> None:
        """Стереть копию лаб и профилей этого пользователя на компьютере (общий компьютер в колледже)."""
        import shutil

        for d in (self.local.home / "labs" / safe_name(self.auth.user_id), self._profiles_cache()):
            shutil.rmtree(d, ignore_errors=True)

    # участники (для администратора)
    def member(self) -> Member | None:
        rows = self._rest("GET", "members", params={"select": "*", "user_id": f"eq.{self.auth.user_id}"}).json()
        return Member.model_validate(rows[0]) if rows else None

    def list_members(self) -> list[Member]:
        rows = self._rest("GET", "members", params={"select": "*", "order": "created_at.asc"}).json()
        return [Member.model_validate(r) for r in rows]

    def set_approved(self, user_id: str, approved: bool) -> None:
        self._rest("PATCH", "members", params={"user_id": f"eq.{user_id}"}, json={"approved": approved},
                   prefer="return=minimal")

    def usage_summary(self) -> dict[str, int]:
        rows = self._rest("GET", "llm_usage", params={"select": "user_id,prompt_tokens,completion_tokens"}).json()
        out: dict[str, int] = {}
        for r in rows:
            out[r["user_id"]] = out.get(r["user_id"], 0) + int(r["prompt_tokens"]) + int(r["completion_tokens"])
        return out

    # админ-панель
    _MEMBER_FIELDS = {"display_name", "approved", "access_until", "allow_shared_key"}
    _LAB_COLUMNS = "owner,profile_id,subject_id,number,status,created_at,updated_at"

    def _rpc(self, fn: str, args: dict | None = None):
        r = self._rest("POST", f"rpc/{fn}", json=args or {})
        return r.json() if r.content else None

    def admin_overview(self) -> AdminOverview:
        members = self.list_members()
        keys = {r["user_id"]: KeyInfo.model_validate(r) for r in self._rpc("admin_keys") or []}
        daily = [(str(r["day"]), r["user_id"], int(r["tokens"])) for r in self._rpc("admin_usage_daily", {"days": 30}) or []]
        usage: dict[str, int] = {}
        for _, uid, tokens in daily:
            usage[uid] = usage.get(uid, 0) + tokens
        labs = [LabRow.model_validate(r) for r in
                self._rest("GET", "labs", params={"select": self._LAB_COLUMNS, "order": "created_at.desc"}).json()]
        profiles: dict[str, list[Profile]] = {}
        for r in self._rest("GET", "student_profiles", params={"select": "owner,data"}).json():
            profiles.setdefault(r["owner"], []).append(Profile.model_validate(r["data"]))
        notes = self._notes()
        for m in members:
            m.note = notes.get(m.user_id, "")
        return AdminOverview(members=members, keys=keys, usage_30d=usage, daily=daily, labs=labs, profiles=profiles)

    def admin_member(self, uid: str) -> MemberCard | None:
        rows = self._rest("GET", "members", params={"select": "*", "user_id": f"eq.{uid}"}).json()
        if not rows:
            return None
        key = next((KeyInfo.model_validate(r) for r in self._rpc("admin_keys") or [] if r["user_id"] == uid), None)
        profiles = [Profile.model_validate(r["data"]) for r in
                    self._rest("GET", "student_profiles", params={"select": "owner,data", "owner": f"eq.{uid}"}).json()]
        labs = [LabRow.model_validate(r) for r in
                self._rest("GET", "labs", params={"select": self._LAB_COLUMNS, "owner": f"eq.{uid}",
                                                  "order": "created_at.desc"}).json()]
        by_lab = {r["lab"]: (int(r["tokens"]), int(r["calls"])) for r in
                  self._rpc("admin_usage_by_lab", {"target": uid}) or []}
        daily = [(str(r["day"]), int(r["tokens"])) for r in self._rpc("admin_usage_daily", {"days": 30}) or []
                 if r["user_id"] == uid]
        events = [MemberEvent.model_validate(r) for r in
                  self._rest("GET", "member_events", params={"select": "*", "user_id": f"eq.{uid}",
                                                             "order": "created_at.desc", "limit": "50"}).json()]
        member = Member.model_validate(rows[0])
        member.note = self._notes(uid).get(uid, "")
        return MemberCard(member=member, key=key, profiles=profiles, labs=labs,
                          usage_by_lab=by_lab, daily=daily, events=events)

    def retag_usage(self, old_lab: str, new_lab: str) -> None:
        """Черновик получил номер: расход его разбора переносится на лабу (только свои строки)."""
        self._rpc("retag_my_usage", {"old_lab": old_lab, "new_lab": new_lab})

    def admin_update_member(self, uid: str, **fields) -> None:
        data = {k: v for k, v in fields.items() if k in self._MEMBER_FIELDS}
        if data:
            self._rest("PATCH", "members", params={"user_id": f"eq.{uid}"}, json=data, prefer="return=minimal")
        if "note" in fields:  # заметка администратора — в отдельной таблице, участнику не видна
            self._upsert("member_notes", {"user_id": uid, "note": fields["note"]}, "user_id")

    def _notes(self, uid: str | None = None) -> dict[str, str]:
        params = {"select": "user_id,note"}
        if uid:
            params["user_id"] = f"eq.{uid}"
        return {r["user_id"]: r["note"] for r in self._rest("GET", "member_notes", params=params).json()}

    def admin_extend(self, uid: str, days: int) -> None:
        """Продлить на N дней: от текущей даты окончания, если она ещё впереди, иначе от сегодня."""
        rows = self._rest("GET", "members", params={"select": "access_until", "user_id": f"eq.{uid}"}).json()
        current = parse_ts(rows[0].get("access_until")) if rows else None
        start = max(current, now_utc()) if current else now_utc()
        self.admin_update_member(uid, access_until=(start + timedelta(days=days)).isoformat())

    def admin_set_until(self, uid: str, day: str | None) -> None:
        """До конца выбранного дня (по Минску); None — бессрочно."""
        self.admin_update_member(uid, access_until=end_of_day_minsk(day) if day else None)

    def admin_revoke(self, uid: str) -> None:
        self.admin_update_member(uid, access_until=now_utc().isoformat())

    def admin_set_key(self, uid: str, api_key: str) -> str:
        key = (api_key or "").strip()
        if len(key) < 8 or any(c.isspace() for c in key):
            raise ValueError("Ключ должен быть одной строкой без пробелов, не короче 8 символов.")
        try:
            return str(self._rpc("admin_set_key", {"target": uid, "api_key": key}))
        except CloudError as e:
            if "key too short" in (e.detail or ""):
                raise ValueError("Ключ должен быть одной строкой без пробелов, не короче 8 символов.") from e
            raise

    def admin_clear_key(self, uid: str) -> None:
        self._rpc("admin_clear_key", {"target": uid})

    def _balance_call(self, body: dict, ids: list[str]) -> dict[str, tuple[int | None, str]]:
        """Остатки у провайдера: (токены, состояние), состояние — ok | no_key | offline."""
        offline = {i: (None, "offline") for i in ids}
        try:
            r = self.http.post(f"{self.cfg.llm_base_url}/balance", json=body, headers=self._h(), timeout=30)
        except httpx.TransportError:
            return offline
        if r.status_code >= 400:
            return offline
        data = r.json() or {}
        balances, errors = data.get("balances") or {}, data.get("errors") or {}
        out: dict[str, tuple[int | None, str]] = {}
        for i in ids:
            value = balances.get(i)
            if value is not None and i not in errors:
                out[i] = (int(value), "ok")
            elif errors.get(i) == "no key":
                out[i] = (None, "no_key")
            elif i == "shared" and i not in balances:
                out[i] = (None, "no_key")  # общий ключ LLM_API_KEY не задан
            else:
                out[i] = (None, "offline")
        return out

    def balances(self, user_ids: list[str], shared: bool = False) -> dict[str, tuple[int | None, str]]:
        """Остаток токенов у провайдера по ключам участников (и общему ключу)."""
        ids = list(user_ids) + (["shared"] if shared else [])
        return self._balance_call({"user_ids": list(user_ids), "shared": shared}, ids)

    def my_balance(self) -> tuple[int | None, str]:
        return self._balance_call({}, [self.auth.user_id])[self.auth.user_id]

    def download_member_report(self, uid: str, profile_id: str, subject_id: str, number: str, local: Path) -> bool:
        key = storage_key(uid, profile_id, subject_id, number, "report.docx")
        try:
            r = self.http.get(f"{self.cfg.url}/storage/v1/object/{BUCKET}/{quote(key)}", headers=self._h())
        except httpx.TransportError as e:
            raise CloudError(offline_message(e), str(e), offline=True) from e
        if r.status_code in (400, 404):
            return False
        self._check(r)
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_bytes(r.content)
        return True

