"""Конвейер лабы: черновик → разбор → подтверждение → выполнение → ручные скриншоты → вёрстка → сдача."""

from __future__ import annotations

import logging
import os
import shutil
import threading
import uuid
from datetime import date as _date
from pathlib import Path
from typing import Callable

from forlabs.core.models import (
    ChatMessage,
    Figure,
    LabSpec,
    LabState,
    Profile,
    ReportDoc,
    is_accepted,
    push_version,
)
from forlabs.core.rules import RuleBook
from forlabs.core.store import safe_file_name, safe_name
from forlabs.core.subjects import get_subject
from forlabs.ingest.extract import SourceText, extract_many
from forlabs.pipeline.chat import plan_edit, revise_answers, revise_section
from forlabs.pipeline.events import EventBus
from forlabs.pipeline.images import (
    MAX_IMAGES,
    describe_images,
    image_ok,
    normalize_screenshot,
    prepare_image,
)
from forlabs.pipeline.repair import check_all, check_and_repair, report_text
from forlabs.pipeline.write import refresh_derived, write_report
from forlabs.render.conformance import compare
from forlabs.render.docx_builder import build_lab, build_semester
from forlabs.render.finalize import finalize, has_slots
from forlabs.spec.extract import extract_spec, verify_verbatim


class PipelineError(Exception):
    pass


DRAFT_PREFIX = "новая-"


def is_draft(number: str) -> bool:
    return number.startswith("новая")


def _now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


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
        self.renamed: dict[str, str] = {}

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

    def journal(self, st: LabState):
        """События из файла журнала лабы (переживают перезапуск приложения)."""
        from forlabs.pipeline.events import Event

        f = self.lab_dir(st) / "journal.log"
        if not f.exists():
            return []
        out = []
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), start=1):
            kind, _, text = line.partition("\t")
            if text:
                out.append(Event(seq=-i, text=text, kind=kind))
        return out[-300:]

    def _llm(self, st: LabState):
        """Клиент модели с меткой лабы: в облаке расход видно по каждой лабе."""
        from urllib.parse import quote

        llm = self.llm_factory()
        if getattr(llm, "accepts_lab_tag", False):  # только прокси ForLabs; провайдеру напрямую имя не шлём
            llm = llm.with_headers({"X-ForLabs-Lab": quote(self.key(st), safe="")})
        return llm

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

    def _sync_assets(self, st: LabState) -> None:
        d = self.lab_dir(st)
        if (d / "assets").exists():
            self._sync(st, [f.relative_to(d).as_posix() for f in sorted((d / "assets").rglob("*")) if f.is_file()])

    def _fetch_missing(self, st: LabState) -> None:
        """Рисунки, которых нет на этом компьютере (лаба делалась на другом), докачиваются из облака."""
        if not st.report:
            return
        d = self.lab_dir(st)
        for sec in st.report.sections:
            for b in sec.blocks:
                if not (isinstance(b, Figure) and b.image_path) or Path(b.image_path).is_absolute():
                    continue
                local = d / b.image_path
                if local.exists():
                    continue
                if hasattr(self.store, "fetch"):
                    self._emit(st, f"Скачиваю из облака {b.image_path}")
                    if self.store.fetch(st.profile_id, st.subject_id, st.number, b.image_path, local):
                        continue
                raise PipelineError(f"Нет файла рисунка «{b.topic}» ({b.image_path}) ни здесь, ни в облаке. "
                                    "Вставь его заново.")

    def _drop_broken_images(self, st: LabState) -> None:
        """Повреждённый или нечитаемый снимок не валит вёрстку: место для него возвращается в «Нужно от тебя»."""
        d = self.lab_dir(st)
        for sec in st.report.sections if st.report else []:
            for b in sec.blocks:
                if isinstance(b, Figure) and b.image_path:
                    f = Path(b.image_path) if Path(b.image_path).is_absolute() else d / b.image_path
                    if f.exists() and not image_ok(f):
                        b.image_path = None
                        b.slot = "Картинка повреждена или в неподходящем формате — вставь снимок ещё раз."
                        self._emit(st, f"Рисунок «{b.topic}» не читается — вставь снимок ещё раз", "warn")

    def _fail(self, st: LabState, e: Exception) -> LabState:
        st.status = "failed"
        st.error = getattr(e, "message_ru", None) or str(e) or type(e).__name__
        self._emit(st, st.error, "error")
        logging.getLogger("forlabs").warning("Лаба %s: %s", self.key(st), st.error, exc_info=e)
        try:
            return self._save(st)
        except Exception as save_error:  # нет связи с облаком: локальная копия и журнал всё равно есть
            self._emit(st, f"Не получилось сохранить состояние: {getattr(save_error, 'message_ru', save_error)}",
                       "error")
            return st

    # шаги
    def _taken(self, profile_id: str, subject_id: str, number: str) -> bool:
        """Номер занят настоящей лабой (папка без состояния — остаток удалённой или прерванной, её можно очистить)."""
        return self.store.load_lab(profile_id, subject_id, number) is not None

    def _save_images(self, st: LabState, images: list[tuple[str, bytes]], prefix: str) -> list[str]:
        """Проверяет и сохраняет фото в notes/; ошибка — PipelineError с понятным текстом, ничего не сохранено."""
        if len(images) > MAX_IMAGES:
            raise PipelineError(f"Не больше {MAX_IMAGES} фото за раз")
        try:
            prepared = [prepare_image(data) for _, data in images]
        except ValueError as e:
            raise PipelineError(str(e)) from e
        rels = []
        for k, data in enumerate(prepared, start=1):
            rel = f"notes/{prefix}_{k}.jpg"
            f = self.lab_dir(st) / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
            rels.append(rel)
        self._sync(st, rels)
        return rels

    def create_lab(self, profile_id: str, subject_id: str, files: list[tuple[str, bytes]],
                   number: str | None = None, pasted_text: str = "", wishes: str = "",
                   wish_images: list[tuple[str, bytes]] | None = None) -> LabState:
        number = (number or "").strip()
        if number and self.store.load_lab(profile_id, subject_id, number) is not None:
            raise PipelineError(f"ЛР {number} уже есть. Открой её или удали, чтобы сделать заново.")
        if not number:
            number = f"{DRAFT_PREFIX}{uuid.uuid4().hex[:6]}"
        st = LabState(profile_id=profile_id, subject_id=subject_id, number=number, status="draft",
                      wishes=(wishes or "").strip()[:4000])
        if wish_images:
            st.wish_images = self._save_images(st, wish_images, "wish")
        src = self.lab_dir(st) / "source"
        shutil.rmtree(src, ignore_errors=True)  # остатки удалённой лабы с тем же номером не смешиваются
        src.mkdir(parents=True, exist_ok=True)
        for name, data in files:
            (src / safe_file_name(name)).write_bytes(data)
        if pasted_text.strip():
            (src / "текст.txt").write_text(pasted_text, encoding="utf-8")
        st.source_path = str(src)
        return self._save(st)

    def parse(self, st: LabState, date: str | None = None) -> LabState:
        try:
            self._emit(st, "Читаю методичку", step="parse")
            src_dir = self.lab_dir(st) / "source"
            files = sorted(f for f in src_dir.iterdir() if f.is_file()) if src_dir.exists() else []
            if files:
                src = extract_many(files)
            elif st.spec and st.spec.source_text.strip():
                # методичка осталась на другом компьютере — её текст сохранён в разборе
                src = SourceText(st.spec.source_text)
            else:
                raise PipelineError("Файлы методички не найдены. Создай лабу заново.")
            for w in src.warnings:
                self._emit(st, w, "warn")
            if not src.text.strip():
                raise PipelineError("В методичке не нашлось текста. Вставь текст вручную.")
            profile = self._profile(st)
            subject = get_subject(st.subject_id)
            enrollment = self._enrollment(profile, st.subject_id)
            self._emit(st, "Разбираю задания и вопросы", step="parse")
            if st.wish_images and not st.wish_images_text:
                self._emit(st, "Читаю фото к пожеланиям", step="parse")
                try:
                    st.wish_images_text = describe_images([self.lab_dir(st) / r for r in st.wish_images],
                                                          self._llm(st))
                    self._save(st)
                except Exception as e:  # фото — подсказка, а не условие разбора
                    self._emit(st, f"Не получилось прочитать фото к пожеланиям ({getattr(e, 'message_ru', e)}) — "
                                   "разбираю без них, текст с фото можно вписать в пожелания", "warn", step="parse")
            wishes = st.wishes or (st.spec.wishes if st.spec else "") or ""
            if st.wish_images_text and st.wish_images_text not in wishes:
                wishes = f"{wishes}\nС фото:\n{st.wish_images_text}".strip()
            spec, findings = extract_spec(src, subject, enrollment, self._llm(st),
                                          None if is_draft(st.number) else st.number, date or today(), wishes=wishes)
            old_dir = self.lab_dir(st)
            if st.number != spec.number:
                old_key = self.key(st)
                st = self._renumber(st, spec.number, old_dir)
                if st.wish_images:
                    self._sync(st, st.wish_images)  # в облаке фото лежали под номером черновика
                spec.number = st.number
                for ev in self.bus.history(old_key):
                    self.bus.publish(self.key(st), ev.text, ev.kind, ev.step)
                self.bus.publish(old_key, st.number, "moved")
            st.spec, st.findings, st.status, st.error = spec, findings, "parsed", None
            self._emit(st, f"Разбор готов: {len(spec.tasks)} заданий, {len(spec.questions)} вопросов", "done",
                       step="parse")
            return self._save(st)
        except Exception as e:  # любая ошибка — в статус лабы, а не в никуда
            return self._fail(st, e)

    def _renumber(self, st: LabState, number: str, old_dir: Path) -> LabState:
        """Переносит черновик под номер из методички. Чужие лабы не трогает: занятый номер → «N-2», «N-3»…"""
        old_key, old_number = self.key(st), st.number
        candidate, k = number, 2
        while self._taken(st.profile_id, st.subject_id, candidate):
            candidate, k = f"{number}-{k}", k + 1
        st = st.model_copy(update={"number": candidate})
        self.renamed[old_key] = candidate
        if hasattr(self.store, "retag_usage"):
            try:  # расход разбора записан под номером черновика — переносим его на лабу
                self.store.retag_usage(old_key, self.key(st))
            except Exception:
                pass
        new_dir = self.lab_dir(st)
        if old_dir.exists():
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            if new_dir.exists():  # папка без состояния (остаток) — иначе черновик окажется внутри неё
                shutil.rmtree(new_dir, ignore_errors=True)
            shutil.move(str(old_dir), str(new_dir))
            st.source_path = str(new_dir / "source")
        try:
            if is_draft(old_number):
                self.store.delete_lab(st.profile_id, st.subject_id, old_number)
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

    def _teacher_text(self, st: LabState) -> str:
        if self.memory is None:
            return ""
        try:
            return self.memory.teacher(st.profile_id, st.subject_id)
        except Exception:
            return ""

    def _memory_text(self, st: LabState) -> str:
        text = ""
        if self.memory is not None:
            try:
                text = self.memory.read(st.profile_id, st.subject_id)
            except Exception:
                text = ""
        if text.strip():
            text = f"Прошлые лабы этого предмета (продолжай ту же тему):\n{text.strip()}"
        teacher = self._teacher_text(st)
        if teacher:
            text = f"Требования преподавателя (соблюдай всегда):\n{teacher}\n\n{text}".strip()
        return text

    # чат правок
    def chat(self, st: LabState, message: str, remember: bool = False,
             images: list[tuple[str, bytes]] | None = None) -> LabState:
        """Сообщение студента о правке: план → точечная правка → проверка → вёрстка; сбой — всё как было."""
        message = (message or "").strip()
        images = images or []
        if not message and not images:
            raise PipelineError("Напиши, что поменять в отчёте, или приложи фото")
        if not st.report or not st.spec:
            raise PipelineError("Чат правок работает после выполнения лабы")
        if not lab_complete(st):
            raise PipelineError("Лаба дописана не до конца — сначала нажми «Продолжить», потом пиши правки.")
        with self.lock(st):
            msg_id = uuid.uuid4().hex[:8]
            rels = self._save_images(st, images, f"chat_{msg_id}") if images else []
            user_msg = ChatMessage(id=msg_id, role="user", text=message[:4000], at=_now(), remember=remember,
                                   images=rels)
            st.chat.append(user_msg)
            self._save(st)
            backup_spec, backup_report = st.spec.model_copy(deep=True), st.report.model_copy(deep=True)
            vid = None
            try:
                profile = self._profile(st)
                subject = get_subject(st.subject_id)
                llm = self._llm(st)
                if rels:
                    self._emit(st, "Читаю приложенные фото", step="write")
                    user_msg.image_text = describe_images([self.lab_dir(st) / r for r in rels], llm)
                    self._save(st)
                    message = f"{message}\n\nНа приложенных фото:\n{user_msg.image_text}".strip()
                self._emit(st, "Разбираю правку", step="write")
                plan = plan_edit(st, message, profile, subject, llm, self.rules, self._teacher_text(st))
                rule = (plan.remember or "").strip() if remember and self.memory is not None else ""
                note = ""
                if remember and self.memory is not None and not rule:
                    note = ("\nНе понял, что запомнить на будущее — напиши требование коротко, например "
                            "«комментарии к каждому методу», и отметь «Запомнить».")

                def remember_rule() -> str:
                    if not rule:
                        return note
                    self.memory.add_teacher(st.profile_id, st.subject_id, rule)
                    return f"\nЗапомнил для следующих лаб: {rule.rstrip('.')}."

                was_final = st.status == "final"
                if plan.kind != "edit":
                    note = remember_rule()
                    st.chat.append(ChatMessage(id=uuid.uuid4().hex[:8], role="assistant", text=plan.reply + note,
                                               at=_now(), kind=plan.kind))
                    self._emit(st, "Ответ в чате правок", "done", step="write")
                    return self._save(st)
                vid = push_version(st, message)
                changed: list[str] = []
                if plan.variant is not None and plan.variant != st.spec.variant:
                    st.spec.variant = plan.variant
                    changed.append(f"вариант {plan.variant}")
                if plan.variant_text and plan.variant_text.strip() != (st.spec.variant_text or ""):
                    st.spec.variant_text = plan.variant_text.strip()
                    changed.append("текст варианта")
                if plan.conclusion:
                    text = " ".join(plan.conclusion.split()).rstrip(".") + "."
                    st.spec.conclusion = text
                    changed.append("вывод")
                memory = self._memory_text(st)
                assets = self.lab_dir(st) / "assets"
                assets.mkdir(parents=True, exist_ok=True)
                retake: list[str] = []
                for edit in plan.tasks:
                    idx = next((i for i, s in enumerate(st.report.sections) if s.n == edit.n), None)
                    if idx is None:
                        continue
                    self._emit(st, f"Правлю задание {edit.n}", step="write")
                    section, again = revise_section(st.spec, st.report.sections[idx], edit.instruction, profile,
                                                    subject, llm, self.rules, assets, memory, self.render_diagrams)
                    st.report.sections[idx] = section
                    retake += again
                    changed.append(f"задание {edit.n}")
                if plan.questions:
                    self._emit(st, "Правлю ответы на вопросы", step="write")
                    st.report.questions = revise_answers(st.spec, st.report.questions, plan.questions, profile,
                                                         subject, llm, self.rules)
                    changed.append("ответы на вопросы " + ", ".join(str(q.n) for q in plan.questions))
                self._emit(st, "Проверяю по стандарту и на признаки ИИ", step="check")
                report, findings = check_and_repair(st.report, st.spec, profile, llm, self.rules, self._others(st),
                                                    on_event=lambda t: self._emit(st, t, step="check"))
                st.report = _relative_images(report, self.lab_dir(st))
                st.findings = findings
                self._sync_assets(st)
                text = plan.reply or "Готово."
                if changed:
                    text += "\nИзменено: " + ", ".join(changed) + "."
                if retake:
                    text += "\nПересними: " + "; ".join(retake) + " — места появились в «Нужно от тебя»."
                if was_final:
                    text += "\nЛаба уже была сдана. Сдай заново, чтобы в папке сдачи был новый файл."
                note = remember_rule()
                reply = ChatMessage(id=uuid.uuid4().hex[:8], role="assistant", text=text + note, at=_now(),
                                    kind="edit", changed=changed, retake=retake, version=vid)
                st.chat.append(reply)
            except Exception as e:  # сбой посреди правки: разбор и отчёт — как до неё
                st.spec, st.report = backup_spec, backup_report
                if vid is not None:
                    st.versions = [v for v in st.versions if v.id != vid]
                reason = getattr(e, "message_ru", None) or str(e) or type(e).__name__
                st.chat.append(ChatMessage(id=uuid.uuid4().hex[:8], role="assistant", at=_now(), kind="failed",
                                           text=f"Не получилось: {reason} Отчёт не изменён — попробуй ещё раз."))
                self._emit(st, f"Правка не удалась: {reason}", "error", step="write")
                return self._save(st)
            self._save(st)  # правка сохранена до вёрстки: выключение во время вёрстки её не потеряет
        st = self.render(st)
        if st.status == "failed":
            reply.text += (f"\nПравку сохранил, но документ не собрался: {st.error} "
                           "Когда исправишь, нажми «Пересобрать документ».")
            st = self._save(st)
        return st

    def revert(self, st: LabState, version_id: int) -> LabState:
        """Вернуть разбор и отчёт к версии; текущее состояние тоже сохраняется версией — откат можно отменить."""
        target = next((v for v in st.versions if v.id == version_id), None)
        if target is None:
            raise PipelineError("Этой версии уже нет: хранятся только 10 последних правок")
        with self.lock(st):
            current = push_version(st, f"до отката к «{target.label}»")
            source = st.spec.source_text
            st.spec, st.report = target.spec.model_copy(deep=True), target.report.model_copy(deep=True)
            st.spec.source_text = source  # в версиях методичка не хранится (облако: меньше данных)
            st.chat.append(ChatMessage(id=uuid.uuid4().hex[:8], role="assistant", at=_now(), kind="revert",
                                       version=current,
                                       text=f"Вернул отчёт к состоянию до правки «{target.label[:80]}»."))
            self._save(st)
        return self.render(st)

    def execute(self, st: LabState, only: list[int] | None = None) -> LabState:
        with self.lock(st):
            try:
                profile = self._profile(st)
                subject = get_subject(st.subject_id)
                enrollment = self._enrollment(profile, st.subject_id)
                if only is None and st.status in ("failed", "executing") and st.report is not None:
                    # прошлый запуск оборвался: дописываем только то, чего ещё нет
                    done = {s.n for s in st.report.sections}
                    only = [t.n for t in st.spec.tasks if t.n not in done]
                    if done:
                        self._emit(st, f"Продолжаю: уже написаны задания {', '.join(map(str, sorted(done)))}",
                                   step="write")
                st.status, st.error = "executing", None
                self._save(st)
                llm = self._llm(st)
                assets = self.lab_dir(st) / "assets"
                assets.mkdir(parents=True, exist_ok=True)

                def progress(doc: ReportDoc) -> None:
                    st.report = _relative_images(doc, self.lab_dir(st))
                    self._save(st)

                report = write_report(st.spec, profile, enrollment, subject, llm, self.rules, assets,
                                      memory=self._memory_text(st),
                                      on_event=lambda t: self._emit(st, t, step="write"),
                                      render_diagrams=self.render_diagrams, existing=st.report, only=only,
                                      on_progress=progress)
                self._emit(st, "Проверяю по стандарту и на признаки ИИ", step="check")
                report, findings = check_and_repair(report, st.spec, profile, llm, self.rules, self._others(st),
                                                    on_event=lambda t: self._emit(st, t, step="check"))
                st.report = _relative_images(report, self.lab_dir(st))
                st.findings = findings
                self._sync_assets(st)
                left = [f for f in findings if f.severity == "error"]
                self._emit(st, "Проверка: замечаний нет" if not left else f"Осталось замечаний: {len(left)}",
                           "done" if not left else "warn", step="check")
                self._save(st)  # отчёт целиком — до вёрстки (Word может работать минуты)
                return self.render(st)
            except Exception as e:  # любая ошибка — в статус лабы, а не в никуда
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
        try:
            image, suffix = normalize_screenshot(image)
        except ValueError as e:
            raise PipelineError(str(e)) from e
        rel = f"assets/fig_{section}_{block}_{uuid.uuid4().hex[:6]}{suffix}"  # новый файл: старые снимки целы
        f = self.lab_dir(st) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(image)
        b.image_path = rel
        self._sync(st, [rel])
        self._emit(st, f"Скриншот для задания {section} добавлен")
        self._save(st)  # скриншот сохранён до вёрстки
        return self.render(st)

    def render(self, st: LabState) -> LabState:
        try:
            if st.spec is not None and not lab_complete(st):
                raise PipelineError("Отчёт дописан не до конца — нажми «Продолжить», чтобы дописать задания и ответы.")
            profile = self._profile(st)
            if st.spec is not None:
                # профиль или разбор могли поменяться после выполнения: шапка, вывод и проверки — по текущим
                st.report = refresh_derived(st.report, st.spec, profile, self._enrollment(profile, st.subject_id))
                st.findings = check_all(st.report, st.spec, profile, self._others(st))
            self._emit(st, "Вёрстка по канону", step="render")
            d = self.lab_dir(st)
            self._fetch_missing(st)
            self._drop_broken_images(st)
            docx = d / "report.docx"
            try:
                build_lab(_absolute_images(st.report, d), docx, author=profile.doc_author or profile.full_name)
            except PermissionError as e:
                raise PipelineError("report.docx открыт в Word или другой программе — закрой его и нажми "
                                    "«Пересобрать документ».") from e
            self._emit(st, "Доводка документа и предпросмотр", step="finalize")
            rep = finalize(docx, profile.doc_author or profile.full_name, d / "render", self.soffice,
                           prefer_word=self.prefer_word)
            # последний шлюз: готовый документ (после доводки) сверяется с эталоном абзац за абзацем
            canon = compare(docx)
            for f in canon:
                if f.location == "continuation":
                    f.severity = "warn"  # вставлено Word при доводке — показываем, но сдачу не держим
            st.findings = [f for f in st.findings if f.rule_id != "render.canon_mismatch"] + canon
            if canon:
                self._emit(st, f"Оформление отличается от эталона в {len(canon)} местах — смотри замечания", "warn",
                           step="render")
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
        if st.status not in ("rendered", "final"):
            raise PipelineError("Сдать можно только свёрстанный отчёт. Сначала нажми «Пересобрать документ».")
        if self.slots(st):
            raise PipelineError("Сначала вставь все скриншоты")
        accepted = set(st.accepted_rules) | set(accept_rules or [])
        left = [f for f in st.findings if f.severity == "error" and not is_accepted(f, accepted)]
        if left:
            raise PipelineError(f"Остались замечания проверок ({len(left)}). Исправь или прими их явно.")
        docx = self.lab_dir(st) / "report.docx"
        if not docx.exists() or has_slots(docx):
            # документ свёрстан на другом компьютере или до вставки скриншотов — свёрстать заново
            st = self.render(st)
            if st.status == "failed" or not docx.exists() or has_slots(docx):
                raise PipelineError(st.error or "Не удалось свёрстать отчёт без мест для скриншотов")
        profile = self._profile(st)
        subject = get_subject(st.subject_id)
        root = Path(profile.output_root) if profile.output_root else Path.home() / "Лабораторные"
        short = safe_name(subject.short_name)
        out_dir = root / short / f"ЛР{safe_name(st.number)}"
        target = out_dir / f"ЛР{safe_name(st.number)}_{short}.docx"
        tmp = target.with_name(target.name + ".tmp")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(docx, tmp)  # сначала копия, потом замена — выключение не оставит обрезанный файл
            os.replace(tmp, target)
        except PermissionError as e:
            tmp.unlink(missing_ok=True)
            raise PipelineError(f"Не получилось записать {target.name}: файл открыт в Word. Закрой его и нажми ещё раз.") from e
        except OSError as e:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise PipelineError(f"Папка сдачи недоступна: {root}. Проверь флешку или диск либо выбери другую папку "
                                f"в профиле. ({getattr(e, 'strerror', '') or e})") from e
        st.accepted_rules = sorted(accepted)
        st.status = "final"
        self._emit(st, f"Готово: {target}", "done")
        st = self._save(st)  # сдано — сразу; память предмета обновляется в фоне (запрос к модели может быть долгим)
        if self.memory is not None:
            snapshot, llm = st.model_copy(deep=True), self._llm(st)

            def update_memory() -> None:
                try:
                    self.memory.update(snapshot, llm)
                except Exception as e:
                    logging.getLogger("forlabs").warning("Память предмета не обновлена: %s", e)

            threading.Thread(target=update_memory, name="memory-update", daemon=True).start()
        return st

    def build_semester(self, profile_id: str, subject_id: str, numbers: list[str], out: Path) -> tuple[Path, object]:
        profile = self.store.get_profile(profile_id)
        enrollment = self._enrollment(profile, subject_id)
        docs = []
        for n in numbers:
            st = self.store.load_lab(profile_id, subject_id, n)
            if st and st.report:
                if self.slots(st):
                    raise PipelineError(f"В ЛР {n} не вставлены скриншоты — вставь их или убери лабу из отчёта")
                self._fetch_missing(st)
                docs.append(_absolute_images(st.report, self.store.lab_dir(profile_id, subject_id, n)))
        if not docs:
            raise PipelineError("Нет готовых лаб для отчёта за семестр")
        out = Path(out)
        build_semester(docs, profile, enrollment, out)
        rep = finalize(out, profile.doc_author or profile.full_name, out.parent / "render_semester", self.soffice,
                       prefer_word=self.prefer_word)
        return out, rep


def lab_complete(st: LabState) -> bool:
    """Все задания и ответы на вопросы методички есть в отчёте."""
    if not st.spec or not st.report:
        return False
    written = {s.n for s in st.report.sections}
    if any(t.n not in written for t in st.spec.tasks):
        return False
    return len(st.report.questions) >= len(st.spec.questions)


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
