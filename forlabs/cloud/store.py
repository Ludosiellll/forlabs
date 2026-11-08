"""Облачное хранилище: те же операции, что у локального Store, но данные в Supabase (RLS: только свои),
файлы — в приватном bucket. Локальная папка служит кэшем для файлов."""

from __future__ import annotations

import hashlib
import re
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
from forlabs.core.models import LabState, Profile
from forlabs.core.store import Store, _number_key, safe_name

BUCKET = "labs"


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
    def __init__(self, auth: CloudAuth, cache_home: Path | None = None):
        self.auth = auth
        self.http = auth.http
        self.cfg = auth.config
        self.local = Store(cache_home or paths.data_home() / "cloud_cache")

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

    def _rest(self, method: str, table: str, params: dict | None = None, json=None, prefer: str | None = None):
        try:
            r = self.http.request(method, f"{self.cfg.url}/rest/v1/{table}", params=params, json=json,
                                  headers=self._h({"Prefer": prefer} if prefer else None))
        except httpx.TransportError as e:
            raise CloudError("Нет связи с облаком. Проверь интернет.", str(e)) from e
        return self._check(r)

    def _upsert(self, table: str, row: dict, on_conflict: str) -> None:
        self._rest("POST", table, params={"on_conflict": on_conflict}, json=row,
                   prefer="resolution=merge-duplicates,return=minimal")

    # профили
    def list_profiles(self) -> list[Profile]:
        rows = self._rest("GET", "student_profiles", params={"select": "data"}).json()
        return sorted((Profile.model_validate(r["data"]) for r in rows), key=lambda p: p.full_name)

    def get_profile(self, pid: str) -> Profile | None:
        rows = self._rest("GET", "student_profiles", params={"select": "data", "id": f"eq.{pid}"}).json()
        return Profile.model_validate(rows[0]["data"]) if rows else None

    def save_profile(self, p: Profile) -> None:
        self._upsert("student_profiles", {"id": p.id, "data": p.model_dump(mode="json")}, "owner,id")

    def delete_profile(self, pid: str) -> None:
        self._rest("DELETE", "student_profiles", params={"id": f"eq.{pid}"})

    # лабы
    def lab_dir(self, profile_id: str, subject_id: str, number: str) -> Path:
        return self.local.home / "labs" / safe_name(self.auth.user_id) / safe_name(profile_id) / \
            safe_name(subject_id) / f"ЛР{safe_name(number)}"

    def save_lab(self, st: LabState) -> None:
        from datetime import datetime

        st.updated_at = datetime.now().isoformat(timespec="seconds")
        d = self.lab_dir(st.profile_id, st.subject_id, st.number)
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(st.model_dump_json(indent=2), encoding="utf-8")
        self._upsert("labs", {"profile_id": st.profile_id, "subject_id": st.subject_id, "number": st.number,
                              "status": st.status, "state": st.model_dump(mode="json")},
                     "owner,profile_id,subject_id,number")

    def load_lab(self, profile_id: str, subject_id: str, number: str) -> LabState | None:
        rows = self._rest("GET", "labs", params={"select": "state", "profile_id": f"eq.{profile_id}",
                                                 "subject_id": f"eq.{subject_id}", "number": f"eq.{number}"}).json()
        return LabState.model_validate(rows[0]["state"]) if rows else None

    def list_labs(self, profile_id: str, subject_id: str) -> list[LabState]:
        rows = self._rest("GET", "labs", params={"select": "state", "profile_id": f"eq.{profile_id}",
                                                 "subject_id": f"eq.{subject_id}"}).json()
        return sorted((LabState.model_validate(r["state"]) for r in rows), key=lambda s: _number_key(s.number))

    def delete_lab(self, profile_id: str, subject_id: str, number: str) -> None:
        self._rest("DELETE", "labs", params={"profile_id": f"eq.{profile_id}", "subject_id": f"eq.{subject_id}",
                                             "number": f"eq.{number}"})

    # память предмета и правила
    def read_memory(self, profile_id: str, subject_id: str) -> str:
        rows = self._rest("GET", "subject_memory", params={"select": "text", "profile_id": f"eq.{profile_id}",
                                                           "subject_id": f"eq.{subject_id}"}).json()
        return rows[0]["text"] if rows else ""

    def write_memory(self, profile_id: str, subject_id: str, text: str) -> None:
        self._upsert("subject_memory", {"profile_id": profile_id, "subject_id": subject_id, "text": text},
                     "owner,profile_id,subject_id")

    def rule_overrides(self) -> dict[str, str]:
        rows = self._rest("GET", "rule_overrides", params={"select": "path,text"}).json()
        return {r["path"]: r["text"] for r in rows}

    def save_rule_override(self, path: str, text: str) -> None:
        self._upsert("rule_overrides", {"path": path, "text": text}, "owner,path")

    def reset_rule_override(self, path: str) -> None:
        self._rest("DELETE", "rule_overrides", params={"path": f"eq.{path}"})

    # свои предметы (общие для всех компьютеров аккаунта)
    def list_custom_subjects(self) -> list:
        from forlabs.core.subjects import Subject

        rows = self._rest("GET", "custom_subjects", params={"select": "id,data"}).json()
        return [Subject.model_validate(r["data"]) for r in rows]

    def save_custom_subject(self, subject) -> None:
        self._upsert("custom_subjects", {"id": subject.id, "data": subject.model_dump(mode="json")}, "owner,id")

    # файлы
    def upload(self, local: Path, profile_id: str, subject_id: str, number: str, rel: str) -> str:
        key = storage_key(self.auth.user_id, profile_id, subject_id, number, rel)
        try:
            r = self.http.post(f"{self.cfg.url}/storage/v1/object/{BUCKET}/{quote(key)}", content=Path(local).read_bytes(),
                               headers=self._h({"x-upsert": "true", "Content-Type": "application/octet-stream"}))
        except httpx.TransportError as e:
            raise CloudError("Нет связи с облаком. Проверь интернет.", str(e)) from e
        self._check(r)
        return key

    def download(self, key: str, local: Path) -> Path:
        try:
            r = self.http.get(f"{self.cfg.url}/storage/v1/object/{BUCKET}/{quote(key)}", headers=self._h())
        except httpx.TransportError as e:
            raise CloudError("Нет связи с облаком. Проверь интернет.", str(e)) from e
        self._check(r)
        local = Path(local)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(r.content)
        return local

    def fetch(self, profile_id: str, subject_id: str, number: str, rel: str, local: Path) -> bool:
        """Скачивает файл лабы по её пути (assets/…). False — такого файла в облаке нет."""
        key = storage_key(self.auth.user_id, profile_id, subject_id, number, rel)
        try:
            r = self.http.get(f"{self.cfg.url}/storage/v1/object/{BUCKET}/{quote(key)}", headers=self._h())
        except httpx.TransportError as e:
            raise CloudError("Нет связи с облаком. Проверь интернет.", str(e)) from e
        if r.status_code == 404 or (r.status_code == 400 and "not_found" in r.text.replace(" ", "_").lower()):
            return False
        self._check(r)
        local = Path(local)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(r.content)
        return True

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
    _MEMBER_FIELDS = {"display_name", "note", "approved", "access_until", "allow_shared_key"}
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
        return MemberCard(member=Member.model_validate(rows[0]), key=key, profiles=profiles, labs=labs,
                          usage_by_lab=by_lab, daily=daily, events=events)

    def admin_update_member(self, uid: str, **fields) -> None:
        data = {k: v for k, v in fields.items() if k in self._MEMBER_FIELDS}
        if data:
            self._rest("PATCH", "members", params={"user_id": f"eq.{uid}"}, json=data, prefer="return=minimal")

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

    def _balance_call(self, body: dict) -> dict:
        try:
            r = self.http.post(f"{self.cfg.llm_base_url}/balance", json=body, headers=self._h(), timeout=30)
        except httpx.TransportError:
            return {}
        if r.status_code >= 400:
            return {}
        return (r.json() or {}).get("balances") or {}

    def balances(self, user_ids: list[str], shared: bool = False) -> dict[str, int | None]:
        """Остаток токенов у провайдера по ключам участников (None — нет ключа или нет ответа)."""
        return self._balance_call({"user_ids": list(user_ids), "shared": shared})

    def my_balance(self) -> int | None:
        return self._balance_call({}).get(self.auth.user_id)

    def download_member_report(self, uid: str, profile_id: str, subject_id: str, number: str, local: Path) -> bool:
        key = storage_key(uid, profile_id, subject_id, number, "report.docx")
        try:
            r = self.http.get(f"{self.cfg.url}/storage/v1/object/{BUCKET}/{quote(key)}", headers=self._h())
        except httpx.TransportError as e:
            raise CloudError("Нет связи с облаком. Проверь интернет.", str(e)) from e
        if r.status_code in (400, 404):
            return False
        self._check(r)
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_bytes(r.content)
        return True

