"""Модели данных: профиль, разбор методички, отчёт в виде блоков, замечания, состояние лабы."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, PrivateAttr

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
    wishes: str | None = None  # пожелания и отклонения от методички (от студента или преподавателя)
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


class AppTask(BaseModel):
    """Рисунок, который ForLabs снимет сам, работая в программе студента (Visual Studio, SSMS, Packet Tracer…)."""
    app: str  # ключ из forlabs.agent.apps.APPS
    goal: str  # что сделать в программе
    shot: str = ""  # что должно быть видно на снимке (проверка снимка); пусто — goal
    open: list[str] = Field(default_factory=list)  # файлы лабы, которые открыть (пути от папки лабы)
    allow: list[str] = Field(default_factory=list)  # «опасные» кнопки, нужные именно здесь («Удалить»)


class Figure(BaseModel):
    type: Literal["figure"] = "figure"
    topic: str
    image_path: str | None = None
    slot: str | None = None
    width_cm: float | None = None
    auto_note: str | None = None  # почему ForLabs не снял сам (студенту в «Нужно от тебя»)
    need_tool: str | None = None  # чего не хватает на компьютере (dotnet, python…; app:ключ — программа агента)
    app_task: AppTask | None = None  # снимет агент в программе (кнопка «Сделать снимки в программах»)


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


PROFILE_FIELDS: dict[str, str] = {  # что чат правок может предложить поменять в профиле (с подтверждением)
    "full_name": "ФИО", "title_name": "ФИО на титуле", "gender": "пол", "group": "группа",
    "teacher": "руководитель", "title_subject": "название предмета на титуле", "academic_year": "учебный год",
    "variant": "вариант по умолчанию", "workplace": "номер рабочего места",
}


class ProfileChange(BaseModel):
    """Предложение чата поменять данные профиля или предмета: применяется только по кнопке."""
    field: str
    value: str
    old: str = ""
    label: str = ""


class ChatMessage(BaseModel):
    """Сообщение чата правок лабы."""
    id: str
    role: Literal["user", "assistant"]
    text: str
    at: str
    remember: bool = False
    kind: Literal["edit", "answer", "question", "offtopic", "failed", "revert", "note"] = "note"
    changed: list[str] = Field(default_factory=list)
    retake: list[str] = Field(default_factory=list)
    version: int | None = None  # версия «до этой правки» — для кнопки «Вернуть как было»
    images: list[str] = Field(default_factory=list)  # приложенные фото (пути внутри папки лабы)
    image_text: str = ""  # что модель прочитала с фото
    profile: list[ProfileChange] = Field(default_factory=list)  # предложено поменять в профиле
    profile_state: Literal["", "applied", "declined"] = ""


class LabVersion(BaseModel):
    id: int
    at: str
    label: str
    spec: LabSpec
    report: ReportDoc


MAX_VERSIONS = 10


class Material(BaseModel):
    """Файл к лабе от преподавателя (данные, текст, заготовка кода) — текстом, модель читает его целиком."""

    name: str
    text: str


class ProjectInfo(BaseModel):
    """Проект программы лабы: что собрано, где лежит, что мешает снимкам."""
    name: str
    language: str
    kind: str
    built: bool = False
    fixes: int = 0  # кругов исправления ошибок сборки
    missing: list[str] = Field(default_factory=list)  # нет программ на компьютере (ключи: dotnet, python…)
    blocked: list[str] = Field(default_factory=list)  # почему не запускается сам (опасный код)
    note: str = ""  # итог для студента
    folder: str = ""  # папка проекта в папке сдачи


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
    docx_sha: str | None = None  # отпечаток report.docx последней удачной вёрстки; None — документ устарел
    render_inputs: str | None = None  # отпечаток профиля на момент вёрстки
    pdf_path: str | None = None
    pages: list[str] = Field(default_factory=list)
    engine: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    chat: list[ChatMessage] = Field(default_factory=list)
    versions: list[LabVersion] = Field(default_factory=list)
    wishes: str = ""  # пожелания, введённые при создании (до разбора)
    linked: list[str] = Field(default_factory=list)  # номера прошлых лаб, на которых строится эта (выбрал студент)
    linked_texts: dict[str, str] = Field(default_factory=dict)  # их текст из прикреплённого отчёта (только выбранные)
    linked_how: dict[str, str] = Field(default_factory=dict)  # каких нашла модель (заголовка не было): «по смыслу» и т.п.
    materials: list[Material] = Field(default_factory=list)  # файлы к лабе (данные, текст, код) — целиком в модель
    project: ProjectInfo | None = None  # программа лабы: собрана ли, где лежит
    wish_images: list[str] = Field(default_factory=list)  # фото к пожеланиям
    wish_images_text: str = ""  # что модель прочитала с этих фото
    rev: int = 0  # номер записи в облаке: запись с другого компьютера не затирается молча
    write_id: str = ""  # метка последней записи: повтор после обрыва узнаёт свою же запись
    lease_machine: str | None = None  # какой компьютер сейчас выполняет лабу
    lease_until: str | None = None  # до какого времени (UTC) это считается правдой
    pending_uploads: list[str] = Field(default_factory=list)  # файлы, не ушедшие в облако из-за обрыва
    _offline_copy: bool = PrivateAttr(default=False)  # прочитана из копии без связи — только смотреть
    _attempted: str | None = PrivateAttr(default=None)  # write_id записи, ответ на которую потерялся


class LabSummary(BaseModel):
    """Строка списка лаб на главной: без отчёта, версий и методички."""
    profile_id: str
    subject_id: str
    number: str
    status: str
    updated_at: str = ""
    topic: str | None = None


def summarize(st: "LabState") -> LabSummary:
    return LabSummary(profile_id=st.profile_id, subject_id=st.subject_id, number=st.number, status=st.status,
                      updated_at=st.updated_at, topic=st.spec.topic if st.spec else None)


def push_version(st: "LabState", label: str) -> int:
    """Снимок разбора и отчёта перед правкой; хранится не больше MAX_VERSIONS последних."""
    vid = (st.versions[-1].id if st.versions else 0) + 1
    spec = st.spec.model_copy(deep=True)
    spec.source_text = ""  # методичка не меняется правками — не дублируем её в каждой версии
    st.versions.append(LabVersion(id=vid, at=datetime.now().isoformat(timespec="seconds"), label=label[:120],
                                  spec=spec, report=st.report.model_copy(deep=True)))
    del st.versions[:-MAX_VERSIONS]
    return vid
