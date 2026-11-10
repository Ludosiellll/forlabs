"""Предметы: встроенные и добавленные пользователем. Оформление у всех общее."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel

from forlabs.core import paths
from forlabs.core.fs import atomic_write_text, quarantine

SubjectKind = Literal["theory", "code", "1c", "network"]


class Subject(BaseModel):
    id: str
    name: str
    short_name: str
    kind: SubjectKind = "theory"
    default_header_style: Literal["long", "short"] = "long"
    default_variant: int = 14


SUBJECTS: dict[str, Subject] = {s.id: s for s in [
    Subject(id="ssp", name="Современные системы программирования", short_name="ССП", kind="code"),
    Subject(id="kpiyap", name="Конструирование программ и языки программирования", short_name="КПИЯП",
            kind="code", default_variant=30),
    Subject(id="psip", name="Программные средства создания интернет-приложений", short_name="ПССИП",
            kind="code", default_header_style="short"),
    Subject(id="vp", name="Веб-программирование", short_name="ВП", kind="code"),
    Subject(id="trpo", name="Технология разработки программного обеспечения", short_name="ТРПО", kind="code"),
    Subject(id="1c", name="Программирование 1С", short_name="1С", kind="1c"),
    Subject(id="ks", name="Компьютерные сети", short_name="КС", kind="network",
            default_header_style="short", default_variant=2),
]}


def _custom_file():
    return paths.data_home() / "subjects.json"


def custom_subjects() -> dict[str, Subject]:
    f = _custom_file()
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return {d["id"]: Subject.model_validate(d) for d in data}
    except (ValueError, TypeError, KeyError, UnicodeDecodeError) as e:  # обрезан — свои предметы придут из облака
        quarantine(f, str(e)[:120])
        return {}


def all_subjects() -> dict[str, Subject]:
    return {**SUBJECTS, **custom_subjects()}


def add_custom_subject(subject: Subject) -> None:
    items = custom_subjects()
    if items.get(subject.id) == subject:
        return  # не переписываем файл без нужды (синхронизация с облаком на каждом открытии главной)
    items[subject.id] = subject
    atomic_write_text(_custom_file(), json.dumps([s.model_dump() for s in items.values()], ensure_ascii=False,
                                                 indent=2))


def get_subject(subject_id: str) -> Subject:
    subjects = all_subjects()
    if subject_id not in subjects:
        raise KeyError(f"Нет предмета «{subject_id}»")
    return subjects[subject_id]
