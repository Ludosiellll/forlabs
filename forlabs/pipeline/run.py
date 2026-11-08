"""Конвейер лабы: черновик → разбор → подтверждение → выполнение → ручные скриншоты → вёрстка → сдача."""

from __future__ import annotations

import shutil
import threading
from datetime import date as _date
from pathlib import Path
from typing import Callable

from forlabs.core.llm import LLMError
from forlabs.core.models import Figure, LabSpec, LabState, Profile, ReportDoc
from forlabs.core.rules import RuleBook
from forlabs.core.store import safe_name
from forlabs.core.subjects import get_subject
from forlabs.ingest.extract import extract_many
from forlabs.pipeline.events import EventBus
from forlabs.pipeline.repair import check_and_repair, report_text
from forlabs.pipeline.write import write_report
from forlabs.render.docx_builder import build_lab, build_semester
from forlabs.render.finalize import finalize
from forlabs.spec.extract import extract_spec, verify_verbatim


class PipelineError(Exception):
    pass


def today() -> str:
    return _date.today().strftime("%d.%m.%Y")


class Pipeline:
    def __init__(self, store, llm_factory: Callable[[], object], rules: RuleBook | None = None,
                 bus: EventBus | None = None, render_diagrams: bool = True, prefer_word: bool = True,
                 soffice: str | None = None, memory=None):
        self.store = store
        self.llm_factory = llm_factory
        self.rules = rules or RuleBook()
        self.bus = bus or EventBus()
        self.render_diagrams = render_diagrams
        self.prefer_word = prefer_word
        self.soffice = soffice
        self.memory = memory
        self._locks: dict[str, threading.Lock] = {}

    # служебное
    def key(self, st: LabState) -> str:
        return f"{st.profile_id}/{st.subject_id}/{st.number}"

    def lock(self, st: LabState) -> threading.Lock:
        return self._locks.setdefault(self.key(st), threading.Lock())

    def _emit(self, st: LabState, text: str, kind: str = "info", step: str | None = None) -> None:
        self.bus.publish(self.key(st), text, kind, step)
        try:
            d = self.lab_dir(st)
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "journal.log", "a", encoding="utf-8") as f:
                f.write(f"{kind}\t{text}\n")
        except OSError:
            pass

    def lab_dir(self, st: LabState) -> Path:
        return self.store.lab_dir(st.profile_id, st.subject_id, st.number)

    def _profile(self, st: LabState) -> Profile:
        p = self.store.get_profile(st.profile_id)
        if p is None:
            raise PipelineError("Профиль не найден")
        return p

    def _enrollment(self, profile: Profile, subject_id: str):
        from forlabs.core.models import Enrollment

        subject = get_subject(subject_id)
        return profile.enrollment(subject_id) or Enrollment(
            subject_id=subject_id, title_subject=subject.name, variant=subject.default_variant,
            header_style=subject.default_header_style)

    def _save(self, st: LabState) -> LabState:
        self.store.save_lab(st)
        return st

    def _sync(self, st: LabState, rels: list[str]) -> None:
        if hasattr(self.store, "upload"):
            for rel in rels:
                f = self.lab_dir(st) / rel
                if f.exists():
                    try:
                        self.store.upload(f, st.profile_id, st.subject_id, st.number, rel)
                    except Exception as e:  # облако не должно ломать локальную работу
                        self._emit(st, f"Не удалось отправить файл в облако: {e}", "warn")

    def _fail(self, st: LabState, e: Exception) -> LabState:
        st.status = "failed"
        st.error = getattr(e, "message_ru", None) or str(e)
        self._emit(st, st.error, "error")
        return self._save(st)

    # шаги
    def create_lab(self, profile_id: str, subject_id: str, files: list[tuple[str, bytes]],
                   number: str | None = None, pasted_text: str = "") -> LabState:
        st = LabState(profile_id=profile_id, subject_id=subject_id, number=number or "новая", status="draft")
        src = self.lab_dir(st) / "source"
        src.mkdir(parents=True, exist_ok=True)
        for name, data in files:
            (src / safe_name(name)).write_bytes(data)
        if pasted_text.strip():
            (src / "текст.txt").write_text(pasted_text, encoding="utf-8")
        st.source_path = str(src)
        return self._save(st)

    def parse(self, st: LabState, date: str | None = None) -> LabState:
        try:
            self._emit(st, "Читаю методичку", step="parse")
            files = sorted(Path(st.source_path).iterdir()) if st.source_path else []
            src = extract_many(files)
            for w in src.warnings:
                self._emit(st, w, "warn")
            if not src.text.strip():
                raise PipelineError("В методичке не нашлось текста. Вставь текст вручную.")
            profile = self._profile(st)
            subject = get_subject(st.subject_id)
            enrollment = self._enrollment(profile, st.subject_id)
            self._emit(st, "Разбираю задания и вопросы", step="parse")
            spec, findings = extract_spec(src, subject, enrollment, self.llm_factory(),
                                          None if st.number == "новая" else st.number, date or today())
            old_dir = self.lab_dir(st)
            if st.number != spec.number:
                st = self._renumber(st, spec.number, old_dir)
            st.spec, st.findings, st.status, st.error = spec, findings, "parsed", None
            self._emit(st, f"Разбор готов: {len(spec.tasks)} заданий, {len(spec.questions)} вопросов", "done",
                       step="parse")
            return self._save(st)
        except (LLMError, PipelineError, ValueError) as e:
            return self._fail(st, e)

    def _renumber(self, st: LabState, number: str, old_dir: Path) -> LabState:
        st = st.model_copy(update={"number": number})
        new_dir = self.lab_dir(st)
        if old_dir.exists() and old_dir != new_dir:
            if new_dir.exists():
                shutil.rmtree(new_dir)
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_dir), str(new_dir))
            st.source_path = str(new_dir / "source")
            try:
                if hasattr(self.store, "delete_lab"):
                    self.store.delete_lab(st.profile_id, st.subject_id, "новая")
            except Exception:
                pass
        return st

    def confirm(self, st: LabState, spec: LabSpec | None) -> LabState:
        if spec is not None:
            st.spec = spec
        if st.spec is None:
            raise PipelineError("Сначала разбери методичку")
        st.findings = verify_verbatim(st.spec, _SourceProxy(st.spec.source_text))
        st.status = "confirmed"
        return self._save(st)

    def _others(self, st: LabState) -> dict[str, str]:
        out = {}
        try:
            for p in self.store.list_profiles():
                if p.id == st.profile_id:
                    continue
                o = self.store.load_lab(p.id, st.subject_id, st.number)
                if o and o.report:
                    out[p.full_name] = report_text(o.report)
        except Exception:
            pass
        return out

    def _memory_text(self, st: LabState) -> str:
        if self.memory is not None:
            try:
                return self.memory.read(st.profile_id, st.subject_id)
            except Exception:
                return ""
        return ""

    def execute(self, st: LabState, only: list[int] | None = None) -> LabState:
        with self.lock(st):
            try:
                profile = self._profile(st)
                subject = get_subject(st.subject_id)
                enrollment = self._enrollment(profile, st.subject_id)
                st.status, st.error = "executing", None
                self._save(st)
                llm = self.llm_factory()
                assets = self.lab_dir(st) / "assets"
                assets.mkdir(parents=True, exist_ok=True)
                report = write_report(st.spec, profile, enrollment, subject, llm, self.rules, assets,
                                      memory=self._memory_text(st),
                                      on_event=lambda t: self._emit(st, t, step="write"),
                                      render_diagrams=self.render_diagrams, existing=st.report, only=only)
                self._emit(st, "Проверяю по стандарту и на признаки ИИ", step="check")
                report, findings = check_and_repair(report, st.spec, profile, llm, self.rules, self._others(st),
                                                    on_event=lambda t: self._emit(st, t, step="check"))
                st.report = _relative_images(report, self.lab_dir(st))
                st.findings = findings
                left = [f for f in findings if f.severity == "error"]
                self._emit(st, "Проверка: замечаний нет" if not left else f"Осталось замечаний: {len(left)}",
                           "done" if not left else "warn", step="check")
                return self.render(st)
            except (LLMError, PipelineError, ValueError) as e:
                return self._fail(st, e)

    def redo_task(self, st: LabState, n: int) -> LabState:
        return self.execute(st, only=[n])

    def slots(self, st: LabState) -> list[tuple[int, int, Figure]]:
        out = []
        if st.report:
            for sec in st.report.sections:
                for bi, b in enumerate(sec.blocks, start=1):
                    if isinstance(b, Figure) and not b.image_path:
                        out.append((sec.n, bi, b))
        return out

    def attach_screenshot(self, st: LabState, section: int, block: int, image: bytes, suffix: str = ".png") -> LabState:
        sec = next(s for s in st.report.sections if s.n == section)
        b = sec.blocks[block - 1]
        if not isinstance(b, Figure):
            raise PipelineError("Это не рисунок")
        rel = f"assets/fig_{section}_{block}{suffix}"
        f = self.lab_dir(st) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(image)
        b.image_path = rel
        self._sync(st, [rel])
        self._emit(st, f"Скриншот для задания {section} добавлен")
        return self.render(st)

    def render(self, st: LabState) -> LabState:
        try:
            profile = self._profile(st)
            self._emit(st, "Вёрстка по канону", step="render")
            d = self.lab_dir(st)
            docx = d / "report.docx"
            build_lab(_absolute_images(st.report, d), docx, author=profile.doc_author or profile.full_name)
            self._emit(st, "Доводка документа и предпросмотр", step="finalize")
            rep = finalize(docx, profile.doc_author or profile.full_name, d / "render", self.soffice,
                           prefer_word=self.prefer_word)
            st.docx_path, st.pdf_path = str(docx), str(rep.pdf)
            st.pages = [str(p) for p in rep.pages]
            st.engine, st.warnings = rep.engine, rep.warnings
            st.status = "needs_manual" if self.slots(st) else "rendered"
            self._sync(st, ["report.docx"])
            self._emit(st, "Нужны скриншоты — вставь их на экране лабы" if st.status == "needs_manual"
                       else "Отчёт свёрстан", "done", step="render")
            return self._save(st)
        except Exception as e:  # вёрстка не должна терять текст
            return self._fail(st, e)

    def mark_final(self, st: LabState, accept_rules: list[str] | None = None) -> LabState:
        if self.slots(st):
            raise PipelineError("Сначала вставь все скриншоты")
        accepted = set(st.accepted_rules) | set(accept_rules or [])
        left = [f for f in st.findings if f.severity == "error" and f.rule_id not in accepted]
        if left:
            raise PipelineError(f"Остались замечания проверок ({len(left)}). Исправь или прими их явно.")
        profile = self._profile(st)
        subject = get_subject(st.subject_id)
        root = Path(profile.output_root) if profile.output_root else Path.home() / "Лабораторные"
        out_dir = root / subject.short_name / f"ЛР{safe_name(st.number)}"
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"ЛР{safe_name(st.number)}_{subject.short_name}.docx"
        shutil.copyfile(st.docx_path, target)
        st.accepted_rules = sorted(accepted)
        st.status = "final"
        self._emit(st, f"Готово: {target}", "done")
        if self.memory is not None:
            try:
                self.memory.update(st, self.llm_factory())
            except Exception:
                pass
        return self._save(st)

    def build_semester(self, profile_id: str, subject_id: str, numbers: list[str], out: Path) -> tuple[Path, object]:
        profile = self.store.get_profile(profile_id)
        enrollment = self._enrollment(profile, subject_id)
        docs = []
        for n in numbers:
            st = self.store.load_lab(profile_id, subject_id, n)
            if st and st.report:
                docs.append(_absolute_images(st.report, self.store.lab_dir(profile_id, subject_id, n)))
        if not docs:
            raise PipelineError("Нет готовых лаб для отчёта за семестр")
        out = Path(out)
        build_semester(docs, profile, enrollment, out)
        rep = finalize(out, profile.doc_author or profile.full_name, out.parent / "render_semester", self.soffice,
                       prefer_word=self.prefer_word)
        return out, rep


class _SourceProxy:
    def __init__(self, text: str):
        self.text = text


def _relative_images(doc: ReportDoc, lab_dir: Path) -> ReportDoc:
    doc = doc.model_copy(deep=True)
    for sec in doc.sections:
        for b in sec.blocks:
            if isinstance(b, Figure) and b.image_path:
                p = Path(b.image_path)
                try:
                    b.image_path = p.relative_to(lab_dir).as_posix()
                except ValueError:
                    pass
    return doc


def _absolute_images(doc: ReportDoc, lab_dir: Path) -> ReportDoc:
    doc = doc.model_copy(deep=True)
    for sec in doc.sections:
        for b in sec.blocks:
            if isinstance(b, Figure) and b.image_path and not Path(b.image_path).is_absolute():
                b.image_path = str(lab_dir / b.image_path)
    return doc
