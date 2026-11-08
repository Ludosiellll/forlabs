"""Хранение профилей и состояний лаб в папке данных приложения (JSON-файлы)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from forlabs.core import paths
from forlabs.core.fs import atomic_write_text
from forlabs.core.models import LabState, Profile

_SAFE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё._-]+")


def safe_name(s: str) -> str:
    return _SAFE.sub("_", s).strip("_") or "x"


def safe_file_name(name: str, limit: int = 80) -> str:
    """Имя файла для папки лабы: безопасные символы и не длиннее limit (Windows: путь до 260 символов)."""
    stem, dot, ext = name.rpartition(".")
    if not dot or len(ext) > 8:
        stem, ext = name, ""
    stem = safe_name(stem)[: max(8, limit - len(ext) - 1)].rstrip("_") or "file"
    return f"{stem}.{safe_name(ext)}" if ext else stem


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
        found = (_read_model(Profile, f) for f in d.glob("*.json"))
        return sorted((p for p in found if p is not None), key=lambda p: p.full_name)

    def get_profile(self, pid: str) -> Profile | None:
        return _read_model(Profile, self._profile_file(pid))

    def save_profile(self, p: Profile) -> None:
        atomic_write_text(self._profile_file(p.id), p.model_dump_json(indent=2))

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
        atomic_write_text(d / "state.json", st.model_dump_json(indent=2))

    def load_lab(self, profile_id: str, subject_id: str, number: str) -> LabState | None:
        return _read_model(LabState, self.lab_dir(profile_id, subject_id, number) / "state.json")

    def list_labs(self, profile_id: str, subject_id: str) -> list[LabState]:
        d = self.home / "labs" / safe_name(profile_id) / safe_name(subject_id)
        if not d.exists():
            return []
        states = [_read_model(LabState, x / "state.json") for x in d.iterdir() if (x / "state.json").exists()]
        return sorted((s for s in states if s is not None), key=lambda s: _number_key(s.number))

    def delete_lab(self, profile_id: str, subject_id: str, number: str) -> None:
        import shutil

        d = self.lab_dir(profile_id, subject_id, number)
        if d.exists():
            shutil.rmtree(d)

    def write_json(self, rel: str, data) -> None:
        atomic_write_text(self.home / rel, json.dumps(data, ensure_ascii=False, indent=2))


def _read_model(model, f: Path):
    """Прочитать модель из файла; обрезанный или битый файл — None и запись в журнал (файл не трогаем: его можно
    восстановить вручную)."""
    if not f.exists():
        return None
    try:
        return model.model_validate_json(f.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError, OSError) as e:
        logging.getLogger("forlabs").warning("Не читается %s: %s", f, str(e)[:200])
        return None

