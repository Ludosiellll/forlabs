"""Память предмета: короткая выжимка прошлых лаб (тема сайта, проект, база), чтобы следующие лабы продолжали её."""

from __future__ import annotations

from typing import Callable

from forlabs.core import paths
from forlabs.core.models import LabState
from forlabs.core.store import safe_name
from forlabs.pipeline.write import section_text

MAX_CHARS = 3000
PROMPT = ("Сожми выполненную лабораторную работу в 2–5 коротких строк для памяти следующих работ этого предмета: "
          "выбранная тема/проект/предметная область, ключевые имена (файлы, классы, объекты, страницы), "
          "что уже сделано. Без оценок и воды, по-русски, обычным текстом без markdown.")


class SubjectMemory:
    def __init__(self, read_fn: Callable[[str, str], str] | None = None,
                 write_fn: Callable[[str, str, str], None] | None = None):
        self._read = read_fn or self._read_local
        self._write = write_fn or self._write_local

    @staticmethod
    def _file(profile_id: str, subject_id: str):
        return paths.data_home() / "memory" / safe_name(profile_id) / f"{safe_name(subject_id)}.md"

    def _read_local(self, profile_id: str, subject_id: str) -> str:
        f = self._file(profile_id, subject_id)
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def _write_local(self, profile_id: str, subject_id: str, text: str) -> None:
        f = self._file(profile_id, subject_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")

    def read(self, profile_id: str, subject_id: str) -> str:
        return self._read(profile_id, subject_id)

    def write(self, profile_id: str, subject_id: str, text: str) -> None:
        self._write(profile_id, subject_id, text[-MAX_CHARS:])

    def update(self, st: LabState, llm) -> str:
        if not st.report or not st.spec:
            return self.read(st.profile_id, st.subject_id)
        body = "\n\n".join(f"Задание {s.n}: {s.title}\n{section_text(s)}" for s in st.report.sections)
        summary = llm.chat([{"role": "system", "content": PROMPT},
                            {"role": "user", "content": f"ЛР №{st.number}. Тема: {st.spec.topic}\n\n{body[:20000]}"}],
                           temperature=0.2).strip()
        old = self.read(st.profile_id, st.subject_id)
        lines = [ln for ln in old.splitlines() if not ln.startswith(f"ЛР №{st.number}:")]
        lines.append(f"ЛР №{st.number}: " + " ".join(summary.split()))
        text = "\n".join(lines)
        self.write(st.profile_id, st.subject_id, text)
        return text

    # требования преподавателя: отдельная запись, не обрезается вместе с выжимками лаб
    MAX_TEACHER = 30

    def teacher(self, profile_id: str, subject_id: str) -> str:
        return self._read(profile_id, f"{subject_id}.teacher")

    def add_teacher(self, profile_id: str, subject_id: str, rule: str) -> str:
        rule = " ".join(rule.split()).strip(" -—–").rstrip(".") + "."
        lines = [ln for ln in self.teacher(profile_id, subject_id).splitlines() if ln.strip()]
        if rule.lower() not in {ln.lstrip("- ").lower() for ln in lines}:
            lines.append(f"- {rule}")
        text = "\n".join(lines[-self.MAX_TEACHER:])
        self._write(profile_id, f"{subject_id}.teacher", text)
        return text

