"""Хранение профилей и состояний лаб в папке данных приложения (JSON-файлы)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from forlabs.core import paths
from forlabs.core.models import LabState, Profile

_SAFE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё._-]+")


def safe_name(s: str) -> str:
    return _SAFE.sub("_", s).strip("_") or "x"


def _number_key(n: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", n)]


class Store:
    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else paths.data_home()

    # профили
    def _profile_file(self, pid: str) -> Path:
        return self.home / "profiles" / f"{safe_name(pid)}.json"

    def list_profiles(self) -> list[Profile]:
        d = self.home / "profiles"
        if not d.exists():
            return []
        return sorted((Profile.model_validate_json(f.read_text(encoding="utf-8")) for f in d.glob("*.json")),
                      key=lambda p: p.full_name)

    def get_profile(self, pid: str) -> Profile | None:
        f = self._profile_file(pid)
        return Profile.model_validate_json(f.read_text(encoding="utf-8")) if f.exists() else None

    def save_profile(self, p: Profile) -> None:
        f = self._profile_file(p.id)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(p.model_dump_json(indent=2), encoding="utf-8")

    def delete_profile(self, pid: str) -> None:
        f = self._profile_file(pid)
        if f.exists():
            f.unlink()

    # лабы
    def lab_dir(self, profile_id: str, subject_id: str, number: str) -> Path:
        return self.home / "labs" / safe_name(profile_id) / safe_name(subject_id) / f"ЛР{safe_name(number)}"

    def save_lab(self, st: LabState) -> None:
        d = self.lab_dir(st.profile_id, st.subject_id, st.number)
        d.mkdir(parents=True, exist_ok=True)
        st.updated_at = datetime.now().isoformat(timespec="seconds")
        tmp = d / "state.json.tmp"
        tmp.write_text(st.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(d / "state.json")

    def load_lab(self, profile_id: str, subject_id: str, number: str) -> LabState | None:
        f = self.lab_dir(profile_id, subject_id, number) / "state.json"
        return LabState.model_validate_json(f.read_text(encoding="utf-8")) if f.exists() else None

    def list_labs(self, profile_id: str, subject_id: str) -> list[LabState]:
        d = self.home / "labs" / safe_name(profile_id) / safe_name(subject_id)
        if not d.exists():
            return []
        states = [LabState.model_validate_json((x / "state.json").read_text(encoding="utf-8"))
                  for x in d.iterdir() if (x / "state.json").exists()]
        return sorted(states, key=lambda s: _number_key(s.number))

    def delete_lab(self, profile_id: str, subject_id: str, number: str) -> None:
        import shutil

        d = self.lab_dir(profile_id, subject_id, number)
        if d.exists():
            shutil.rmtree(d)

    def write_json(self, rel: str, data) -> None:
        f = self.home / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
