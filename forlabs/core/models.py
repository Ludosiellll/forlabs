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


LabStatus = Literal["draft", "parsed", "confirmed", "executing", "needs_manual", "rendered", "final", "failed"]


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
