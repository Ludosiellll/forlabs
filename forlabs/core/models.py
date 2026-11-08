"""Модели данных: профиль, разбор методички, отчёт в виде блоков, замечания, состояние лабы."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

Gender = Literal["m", "f"]
HeaderStyle = Literal["long", "short"]


class Enrollment(BaseModel):
    subject_id: str
    title_subject: str
    teacher: str = ""
    academic_year: str = ""
    variant: int | None = None
    workplace: int | None = None  # номер рабочего места/компьютера, если методичка выбирает вариант по нему
    header_style: HeaderStyle = "long"


class Profile(BaseModel):
    id: str
    full_name: str
    title_name: str
    gender: Gender = "m"
    group: str = "РС02-23"
    specialty_code: str = "5-04-0612-02"
    specialty_lines: list[str] = Field(default_factory=lambda: [
        "«Разработка и сопровождение", " программного обеспечения ", " информационных технологий»"])
    college_lines: list[str] = Field(default_factory=lambda: [
        "МИНИСТЕРСТВО ОБРАЗОВАНИЯ РЕСПУБЛИКИ БЕЛАРУСЬ", "ФИЛИАЛ БГТУ",
        "«БОБРУЙСКИЙ ГОСУДАРСТВЕННЫЙ ЛЕСОТЕХНИЧЕСКИЙ КОЛЛЕДЖ»"])
    city: str = "Бобруйск"
    doc_author: str = ""
    output_root: str = ""
    enrollments: list[Enrollment] = Field(default_factory=list)

    def enrollment(self, subject_id: str) -> Enrollment | None:
        return next((e for e in self.enrollments if e.subject_id == subject_id), None)


class Task(BaseModel):
    n: int
    text: str
    kind: Literal["main", "extra"] = "main"


class LabSpec(BaseModel):
    subject_id: str
    number: str
    date: str
    topic: str
    goal: str
    equipment: str | None = None
    assignment: str = ""
    tasks: list[Task] = Field(default_factory=list)
    variant: int | None = None
    workplace: int | None = None
    variant_text: str | None = None
    questions: list[str] = Field(default_factory=list)
    conclusion: str | None = None  # своя формулировка вывода, если цель не начинается с инфинитива
    source_text: str = ""


class Paragraph(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    text: str


class DashList(BaseModel):
    type: Literal["dash_list"] = "dash_list"
    items: list[str]


class NumberedList(BaseModel):
    type: Literal["numbered_list"] = "numbered_list"
    items: list[str]


class Figure(BaseModel):
    type: Literal["figure"] = "figure"
    topic: str
    image_path: str | None = None
    slot: str | None = None
    width_cm: float | None = None


class Listing(BaseModel):
    type: Literal["listing"] = "listing"
    topic: str
    code: str
    language: str = ""


class Table(BaseModel):
    type: Literal["table"] = "table"
    topic: str
    rows: list[list[str]]
    header_rows: int = 1


Block = Annotated[Union[Paragraph, DashList, NumberedList, Figure, Listing, Table], Field(discriminator="type")]


class TaskSection(BaseModel):
    n: int
    title: str
    blocks: list[Block] = Field(default_factory=list)


class QA(BaseModel):
    n: int
    question: str
    answer: str


class ReportDoc(BaseModel):
    lab_number: str
    header: dict[str, str] = Field(default_factory=dict)
    sections: list[TaskSection] = Field(default_factory=list)
    questions: list[QA] = Field(default_factory=list)
    conclusion: str = ""


class Finding(BaseModel):
    stage: str
    rule_id: str
    severity: Literal["error", "warn"] = "error"
    location: str = ""
    text: str = ""
    message: str
    suggestion: str | None = None


def finding_key(f: "Finding") -> str:
    """Ключ конкретного замечания: правило, место и текст. После «Переписать» текст другой — ключ тоже."""
    import hashlib

    digest = hashlib.sha1(f"{f.location}\n{f.text}".encode("utf-8")).hexdigest()[:10]
    return f"{f.rule_id}@{digest}"


def is_accepted(f: "Finding", accepted: "list[str] | set[str]") -> bool:
    return finding_key(f) in accepted


LabStatus = Literal["draft", "parsed", "confirmed", "executing", "needs_manual", "rendered", "final", "failed"]


class ChatMessage(BaseModel):
    """Сообщение чата правок лабы."""
    id: str
    role: Literal["user", "assistant"]
    text: str
    at: str
    remember: bool = False
    kind: Literal["edit", "answer", "question", "failed", "revert", "note"] = "note"
    changed: list[str] = Field(default_factory=list)
    retake: list[str] = Field(default_factory=list)
    version: int | None = None  # версия «до этой правки» — для кнопки «Вернуть как было»


class LabVersion(BaseModel):
    id: int
    at: str
    label: str
    spec: LabSpec
    report: ReportDoc


MAX_VERSIONS = 10


class LabState(BaseModel):
    profile_id: str
    subject_id: str
    number: str
    status: LabStatus = "draft"
    source_path: str | None = None
    spec: LabSpec | None = None
    report: ReportDoc | None = None
    findings: list[Finding] = Field(default_factory=list)
    accepted_rules: list[str] = Field(default_factory=list)
    docx_path: str | None = None
    pdf_path: str | None = None
    pages: list[str] = Field(default_factory=list)
    engine: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    chat: list[ChatMessage] = Field(default_factory=list)
    versions: list[LabVersion] = Field(default_factory=list)


def push_version(st: "LabState", label: str) -> int:
    """Снимок разбора и отчёта перед правкой; хранится не больше MAX_VERSIONS последних."""
    vid = (st.versions[-1].id if st.versions else 0) + 1
    st.versions.append(LabVersion(id=vid, at=datetime.now().isoformat(timespec="seconds"), label=label[:120],
                                  spec=st.spec.model_copy(deep=True), report=st.report.model_copy(deep=True)))
    del st.versions[:-MAX_VERSIONS]
    return vid
