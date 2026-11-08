"""Облачное хранилище: те же операции, что у локального Store, но данные в Supabase (RLS: только свои),
файлы — в приватном bucket. Локальная папка служит кэшем для файлов."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import quote

import httpx
from pydantic import BaseModel

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
