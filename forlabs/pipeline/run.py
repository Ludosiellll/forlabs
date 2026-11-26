"""Конвейер лабы: черновик → разбор → подтверждение → выполнение → ручные скриншоты → вёрстка → сдача."""

from __future__ import annotations

import json
import logging
import re
import os
import shutil
import threading
import uuid
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from forlabs.core.fs import atomic_write_bytes, durable_replace, os_error_text
from forlabs.core.models import (
    ChatMessage,
    Figure,
    Finding,
    Listing,
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
from forlabs.core.text import plural
from forlabs.ingest.extract import SourceText, extract_many, extract_text
from forlabs.pipeline.chat import plan_edit, revise_answers, revise_section
from forlabs.pipeline.events import EventBus
from forlabs.pipeline.images import (
    MAX_IMAGES,
    describe_images,
    image_ok,
    normalize_screenshot,
    prepare_image,
    put_pictures,
    read_source_pictures,
    worth_reading,
)
from forlabs.pipeline.repair import check_all, check_and_repair, report_text
from forlabs.pipeline.write import refresh_derived, section_has_content, write_report
from forlabs.render.conformance import compare
from forlabs.render.docx_builder import build_lab, build_semester
from forlabs.render.finalize import finalize, has_slots
from forlabs.render import freeze
from forlabs.spec.extract import extract_spec, verify_verbatim

JOURNAL_MAX = 256_000  # байт журнала на одну лабу


class PipelineError(Exception):
    pass


DRAFT_PREFIX = "новая-"


def error_text(e: BaseException) -> str:
    """Текст ошибки для студента: по-русски; непредвиденное — с пометкой и ссылкой на журнал."""
    import re
    import zipfile

    if getattr(e, "message_ru", None):
        return e.message_ru
    if isinstance(e, zipfile.BadZipFile):
        return "Файл отчёта повреждён (запись оборвалась) — нажми «Пересобрать документ»."
    if isinstance(e, OSError):
        return os_error_text(e)
    text = str(e).strip()
    if text and re.search("[А-Яа-яЁё]", text):
        return text
    return (f"Непредвиденная ошибка ({type(e).__name__}{': ' + text[:200] if text else ''}). "
            "Подробности — в журнале forlabs.log; попробуй ещё раз.")


def normalize_number(raw: str) -> str:
    """«№ 7», «ЛР 7.», «Лабораторная работа №3» → «7», «3»; «7/8» → «7-8» (косая черта ломает адрес страницы)."""
    import re

    s = re.sub(r"^\s*((лабораторная|практическая)\s+работа|практическое\s+занятие)\s*", "", raw or "", flags=re.I)
    s = re.sub(r"^\s*(лр|пр|пз)\b\.?\s*", "", s, flags=re.I)
    s = re.sub(r"^\s*(№|n|no\.?)\s*", "", s, flags=re.I)
    s = re.sub(r"[\\/]+", "-", s).strip().rstrip(".").strip()
    return re.sub(r"\s+", " ", s)


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
                 soffice: str | None = None, memory=None, tesseract: str | None = None):
        self.store = store
        self.llm_factory = llm_factory
        self.rules = rules or RuleBook()
        self.bus = bus or EventBus()
        self.render_diagrams = render_diagrams
        self.prefer_word = prefer_word
        self.soffice = soffice
        self.tesseract = tesseract  # путь из настроек: установщик Tesseract не добавляет его в PATH
        self.memory = memory
        self.projects = False  # этап «Проект»: собрать программу лабы и снять её окна (в приложении включён)
        self.shooter = None  # кто снимает окна; None — WindowsShooter в Windows
        self.agents = False  # снимки в программах студента (агент): план после этапа «Проект» (в приложении включён)
        self.app_paths: dict[str, str] = {}  # программы, которые студент указал сам (ключ → путь)
        self.app_hooks: tuple = (None, None)  # свернуть и вернуть окно ForLabs на время работы агента
        self.fix_outcome: dict[str, str] = {}  # лаба → чем кончилась просьба чата исправить программу
        self.fix_reason: dict[str, str] = {}  # лаба → почему исправить не вышло (для ответа в чате)
        self.page_renderer = None  # (документ, рабочая папка) → картинки его страниц; None — Word или LibreOffice
        self._locks: dict[str, threading.Lock] = {}
        self.renamed: dict[str, str] = {}
        self.background: set[str] = set()  # фоновая работа после «Сдать» (память предмета): окно спросит при закрытии
        self._bg_lock = threading.Lock()
        self._memory_locks: dict[str, threading.Lock] = {}
        self._threads: list[threading.Thread] = []

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
            f = d / "journal.log"
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(f"{kind}\t{text}\n")
            if f.stat().st_size > JOURNAL_MAX:  # хранится только свежая половина
                tail = f.read_bytes()[-(JOURNAL_MAX // 2):]
                atomic_write_bytes(f, tail[tail.find(b"\n") + 1:])
        except OSError:
            pass

    def journal(self, st: LabState):
        """События из файла журнала лабы (переживают перезапуск приложения)."""
        from forlabs.pipeline.events import Event

        f = self.lab_dir(st) / "journal.log"
        if not f.exists():
            return []
        out = []
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            kind, _, text = line.partition("\t")
            if text:
                out.append(Event(seq=-i, text=text, kind=kind))
        return out[-300:]

    def _llm(self, st: LabState, chat: str | None = None):
        """Клиент модели с меткой лабы (в облаке расход видно по каждой лабе) и, для чата правок, — сообщения
        (пробный доступ на сервере считает сообщения правок на лабу)."""
        from urllib.parse import quote

        llm = self.llm_factory()
        if getattr(llm, "accepts_lab_tag", False):  # только прокси ForLabs; провайдеру напрямую имя не шлём
            tags = {"X-ForLabs-Lab": quote(self.key(st), safe="")}
            if chat:
                tags["X-ForLabs-Chat"] = chat
            llm = llm.with_headers(tags)
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
        if st.status == "executing":  # другой компьютер увидит, что лаба выполняется здесь, а не «прервалась»
            st.lease_machine = machine_id()
            st.lease_until = (datetime.now(timezone.utc) + LEASE).isoformat(timespec="seconds")
        else:
            st.lease_machine = st.lease_until = None
        self.store.save_lab(st)
        return st

    def _sync(self, st: LabState, rels: list[str]) -> None:
        if hasattr(self.store, "upload"):
            for rel in rels:
                f = self.lab_dir(st) / rel
                if f.exists():
                    try:
                        self.store.upload(f, st.profile_id, st.subject_id, st.number, rel)
                        if rel in st.pending_uploads:
                            st.pending_uploads.remove(rel)
                    except Exception as e:  # облако не должно ломать локальную работу: отправим позже
                        if rel not in st.pending_uploads:
                            st.pending_uploads.append(rel)
                        self._emit(st, f"Файл {rel} пока не отправлен в облако ({getattr(e, 'message_ru', e)}) — "
                                       "отправлю при следующей вёрстке", "warn")

    def _retry_pending(self, st: LabState) -> None:
        """Файлы, не ушедшие в облако при обрыве, отправляются снова (исчезнувшие — забываются)."""
        for rel in list(st.pending_uploads):
            if not (self.lab_dir(st) / rel).exists():
                st.pending_uploads.remove(rel)
        if st.pending_uploads:
            self._sync(st, list(st.pending_uploads))

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
                    if f.exists() and image_ok(f) and not _word_image(f):
                        try:  # снимок из старой версии (WebP, CMYK): Word его не примет — перекодировать
                            data, suffix = normalize_screenshot(f.read_bytes())
                            new = f.with_name(f"{f.stem}_{uuid.uuid4().hex[:4]}{suffix}")
                            new.write_bytes(data)
                            b.image_path = new.relative_to(d).as_posix() if new.is_relative_to(d) else str(new)
                            self._sync(st, [b.image_path])
                            continue
                        except Exception:
                            pass
                    if f.exists() and not image_ok(f):
                        b.image_path = None
                        b.slot = "Картинка повреждена или в неподходящем формате — вставь снимок ещё раз."
                        self._emit(st, f"Рисунок «{b.topic}» не читается — вставь снимок ещё раз", "warn")

    def _fail(self, st: LabState, e: Exception) -> LabState:
        st.status = "failed"
        st.error = error_text(e)
        self._emit(st, st.error, "error")
        logging.getLogger("forlabs").warning("Лаба %s: %s", self.key(st), st.error, exc_info=e)
        try:
            return self._save(st)
        except Exception as save_error:  # нет связи с облаком: локальная копия и журнал всё равно есть
            self._emit(st, f"Не получилось сохранить состояние: {getattr(save_error, 'message_ru', save_error)}",
                       "error")
            return st

    # шаги
    @staticmethod
    def _clear_leftover(d: Path) -> None:
        """Папка лабы без читаемого состояния. С файлом состояния (битым) — откладывается в «ЛРN.old-<время>»,
        чтобы снимки и отчёт можно было достать; без него — это остаток удалённой лабы, его можно стереть."""
        if not d.exists():
            return
        if any(d.glob("state*")):
            from datetime import datetime

            aside = d.with_name(f"{d.name}.old-{datetime.now():%Y%m%d-%H%M%S}")
            os.replace(d, aside)
            logging.getLogger("forlabs").warning("Папка %s с нечитаемым состоянием отложена в %s", d, aside.name)
        else:
            shutil.rmtree(d, ignore_errors=True)

    def _taken(self, profile_id: str, subject_id: str, number: str) -> bool:
        """Номер занят настоящей лабой (папка без состояния — остаток удалённой или прерванной, её можно очистить)."""
        return self.store.load_lab(profile_id, subject_id, number, strict=True) is not None

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
                   wish_images: list[tuple[str, bytes]] | None = None, work_kind: str | None = None,
                   task_files: bool | None = None) -> LabState:
        """work_kind — вид работы, выбранный студентом (lab, practice, session); None — как названа в методичке.
        task_files — режим «задания отдельными файлами», выбранный студентом; None — как настроено у предмета."""
        from forlabs.report.derive import WORK_TITLES, key_suffix, lab_label

        number = normalize_number(number or "")
        kind = work_kind if work_kind in WORK_TITLES else None
        if number and (holder := self.store.load_lab(profile_id, subject_id, number, strict=True)) is not None:
            held, shown = work_kind_of(holder), holder.spec.number if holder.spec else number
            if kind and kind != held:  # тот же номер у работы другого вида — это другая работа, а не повтор
                number = f"{number}-{key_suffix(kind)}"
                holder = self.store.load_lab(profile_id, subject_id, number, strict=True)
                held = kind
            if holder is not None:
                raise PipelineError(f"{lab_label(held, shown)} уже есть. Открой её или удали, чтобы сделать заново.")
        if not number:
            number = f"{DRAFT_PREFIX}{uuid.uuid4().hex[:6]}"
        st = LabState(profile_id=profile_id, subject_id=subject_id, number=number, status="draft",
                      wishes=(wishes or "").strip()[:4000], work_kind=kind, task_files=task_files)
        self._clear_leftover(self.lab_dir(st))  # остатки удалённой или нечитаемой лабы с тем же номером
        if wish_images:
            st.wish_images = self._save_images(st, wish_images, "wish")
        src = self.lab_dir(st) / "source"
        src.mkdir(parents=True, exist_ok=True)
        for name, data in files:
            (src / safe_file_name(name)).write_bytes(data)
        if pasted_text.strip():
            (src / "текст.txt").write_text(pasted_text, encoding="utf-8")
        st.source_path = str(src)
        return self._save(st)

    def parse(self, st: LabState, date: str | None = None, number: str | None = None,
              renumber: bool = False) -> LabState:
        """number — номер работы, известный заранее (имя файла, заголовок): модель разбирает только её.
        renumber — лаба переходит под номер из методички, как черновик (после замены методички)."""
        try:
            self._emit(st, "Читаю методичку", step="parse")
            src_dir = self.lab_dir(st) / "source"
            files = sorted(f for f in src_dir.iterdir() if f.is_file()) if src_dir.exists() else []
            if files:
                src = extract_many(files, tesseract=self.tesseract, pictures=True)
            elif st.spec and st.spec.source_text.strip():
                # методичка осталась на другом компьютере — её текст сохранён в разборе
                src = SourceText(st.spec.source_text)
            else:
                raise PipelineError("Файлы методички не найдены. Создай лабу заново.")
            for w in src.warnings:
                self._emit(st, w, "warn")
            unread = 0
            if src.pictures:
                src, unread = self._read_pictures(st, src)
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
            known = None if renumber else st.spec.number if st.spec else (
                None if is_draft(st.number) else plain_number(st.number))
            renumber = renumber or is_draft(st.number)
            if normalize_number(number or ""):
                known = normalize_number(number)
            spec, findings = extract_spec(src, subject, enrollment, self._llm(st), known, date or today(),
                                          wishes=wishes)
            spec.number = normalize_number(spec.number) or "1"
            if st.work_kind:  # студент выбрал вид работы сам: заголовок методички его не меняет
                spec.work_kind = st.work_kind
            if st.spec is not None:  # повторный разбор: то, что студент выбрал на этой работе, остаётся
                spec.task_files, spec.plain_layout = st.spec.task_files, st.spec.plain_layout
            else:
                spec.task_files = st.task_files if st.task_files is not None else bool(enrollment.task_files)
            if known is None:
                findings += _several_labs(src.text, spec.number)
            if unread:
                findings.append(Finding(
                    stage="spec", rule_id="spec.pictures_unread", severity="warn", location="Методичка",
                    message=f"Картинок в методичке, которые не удалось прочитать: {unread}. Нажми «Прочитать "
                            "картинки ещё раз» или, если на них задание, код, запрос или таблица, впиши их текст в "
                            "пожелания к лабе — иначе отчёт выйдет без них"))
            old_dir = self.lab_dir(st)
            old_key = moved_from = None
            if renumber:
                old_key, old_number = self.key(st), st.number
                from forlabs.report.derive import key_suffix, lab_label

                kind = spec.work_kind
                st = self._renumber(st, spec.number, old_dir, kind)
                moved_from = old_number if st.number != old_number else None
                if st.number not in (spec.number, f"{spec.number}-{key_suffix(kind)}"):
                    # номер в документе — из методички; «7-2» только для списка
                    self._emit(st, f"{lab_label(kind, spec.number)} уже есть — эта сохранена как "
                                   f"«{lab_label(kind, st.number)}». Лишнюю можно удалить; в документе номер "
                                   f"останется {spec.number}.", "warn")
            st.spec, st.findings, st.status, st.error = spec, findings, "parsed", None
            st.findings = st.findings + self._link_findings(st)
            # сначала лаба сохраняется под новым номером, потом удаляется черновик, потом страница переходит:
            # обрыв связи или выключение между шагами не теряют лабу
            try:
                self._save(st)
            except Exception:
                if moved_from is not None:  # облако не приняло новую запись — черновик остаётся рабочим
                    new_dir = self.lab_dir(st)
                    if new_dir.exists() and not old_dir.exists():
                        shutil.move(str(new_dir), str(old_dir))
                    self.renamed.pop(old_key, None)
                    st = st.model_copy(update={"number": moved_from, "source_path": str(old_dir / "source")})
                raise
            if moved_from is not None:
                try:  # запись под прежним номером (черновик или лаба до замены методички) — это эта же лаба
                    self.store.delete_lab(st.profile_id, st.subject_id, moved_from)
                except Exception as e:
                    logging.getLogger("forlabs").warning("Запись %s не удалена: %s", moved_from, e)
                if st.wish_images:
                    self._sync(st, st.wish_images)  # в облаке фото лежали под прежним номером
                for ev in self.bus.history(old_key):
                    self.bus.publish(self.key(st), ev.text, ev.kind, ev.step)
                self.bus.publish(old_key, st.number, "moved")
            self._emit(st, "Разбор готов: " + plural(len(spec.tasks), "задание", "задания", "заданий") + ", "
                           + plural(len(spec.questions), "вопрос", "вопроса", "вопросов"), "done",
                       step="parse")
            return self._save(st)
        except Exception as e:  # любая ошибка — в статус лабы, а не в никуда
            return self._fail(st, e)

    def _read_pictures(self, st: LabState, src: SourceText) -> tuple[SourceText, int]:
        """Картинки методички читает модель: текст встаёт на место картинки. Прочитанное хранится рядом с лабой —
        повторный разбор за те же картинки не платит. Возвращает методичку и число непрочитанных картинок."""
        from forlabs.ingest.extract import PICTURE_RE

        import hashlib

        want = worth_reading(src.text, src.pictures)  # иллюстрации к теории не читаются: это минуты ожидания
        cache = self.lab_dir(st) / "source_pictures.json"
        try:
            known = json.loads(cache.read_text(encoding="utf-8"))
            known = {str(k): str(v) for k, v in known.items()} if isinstance(known, dict) else {}
        except (OSError, ValueError):
            known = {}
        before = dict(known)
        order = sorted(want)
        error, got = "", []
        if order:
            rest = len(src.pictures) - len(order)
            self._emit(st, f"Читаю картинки в методичке: {len(order)}"
                           + (f" (ещё {rest} — иллюстрации к теории, их не читаю)" if rest else ""), step="parse")
            got, error = read_source_pictures([src.pictures[i] for i in order], self._llm(st), known)
        if known != before:
            try:
                atomic_write_bytes(cache, json.dumps(known, ensure_ascii=False).encode("utf-8"))
            except OSError:
                pass
        read = dict(zip(order, got))
        # пропущенная иллюстрация убирается из текста; прочитанная раньше — берётся из сохранённого
        texts = [read[i] if i in read else known.get(hashlib.sha256(p.data).hexdigest(), "")
                 for i, p in enumerate(src.pictures)]
        unread = sum(1 for i in order if read.get(i) is None)
        if unread:
            self._emit(st, f"Не прочитал картинок в методичке: {unread} из {len(order)}"
                           + (f" ({error})" if error else "") + " — нажми «Прочитать картинки ещё раз» (прочитанные второй "
                           "раз не читаются) или впиши текст с них в пожелания", "warn", step="parse")
        plain = PICTURE_RE.sub("", src.text).strip()
        if not plain and order and unread == len(order):  # методичка целиком картинкой, и прочитать её не получилось
            why = f": {error.rstrip('. ')}" if error else ""
            raise PipelineError(f"Методичка — это картинка, а прочитать её не получилось{why}. Попробуй ещё раз "
                                "или вставь текст вручную.")
        return SourceText(put_pictures(src.text, src.pictures, texts), src.pages, src.warnings), unread

    def _renumber(self, st: LabState, number: str, old_dir: Path, kind: str = "lab") -> LabState:
        """Переносит черновик под номер из методички. Чужие лабы не трогает: занятый номер → «N-2», «N-3»…
        Свой номер считается свободным: лаба «7-2» с той же методичкой остаётся на месте. Номер занят работой
        другого вида (ЛР 7 и ПЗ 7 в одном предмете) — это не повтор: работа встаёт рядом как «7-пз»."""
        from forlabs.report.derive import key_suffix

        old_key = self.key(st)
        base = number
        if number != st.number:
            holder = self.store.load_lab(st.profile_id, st.subject_id, number, strict=True)
            if holder is not None and work_kind_of(holder) != kind:
                base = f"{number}-{key_suffix(kind)}"
        candidate, k = base, 2
        while candidate != st.number and self._taken(st.profile_id, st.subject_id, candidate):
            candidate, k = f"{base}-{k}", k + 1
        if candidate == st.number:
            return st
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
            self._clear_leftover(new_dir)  # иначе черновик окажется внутри неё
            shutil.move(str(old_dir), str(new_dir))
            st.source_path = str(new_dir / "source")
        return st  # строку черновика удаляет parse — после сохранения под новым номером

    def source_names(self, st: LabState) -> list[str]:
        """Файлы методички в папке лабы (на другом компьютере их нет — пусто)."""
        src = self.lab_dir(st) / "source"
        try:
            return sorted(f.name for f in src.iterdir() if f.is_file()) if src.exists() else []
        except OSError:
            return []

    def replace_source(self, st: LabState, files: list[tuple[str, bytes]], pasted_text: str = "") -> LabState:
        """Другая методичка для этой лабы: файлы в source/ заменяются, разбор — заново. Готовый отчёт уходит в версию
        вместе с прежней методичкой (кнопка «Вернуть как было» возвращает обе); лаба без отчёта переходит под номер
        новой методички, как черновик."""
        from forlabs.core.models import Finding
        from forlabs.pipeline.labnum import PASTED_NAME, read_parts, shown_names, single_number

        pasted_text = pasted_text or ""
        if not files and not pasted_text.strip():
            raise PipelineError("Добавь файл методички или вставь её текст")
        d = self.lab_dir(st)
        staged, src = d / "source.new", d / "source"
        shutil.rmtree(staged, ignore_errors=True)
        staged.mkdir(parents=True, exist_ok=True)
        try:
            for name, data in files:
                (staged / safe_file_name(Path(name or "").name or "файл")).write_bytes(data)
            if pasted_text.strip():
                (staged / PASTED_NAME).write_text(pasted_text, encoding="utf-8")
            parts = read_parts(files, pasted_text)
            had_report = st.report is not None
            current = None if is_draft(st.number) else normalize_number(
                st.spec.number if st.spec else plain_number(st.number))
            detected = single_number(parts)
            renumber = not had_report and not st.versions  # у лабы с отчётом рисунки в облаке лежат под её номером
            number = current
            if renumber:  # номер — из новой методички; если это сборник, где есть и эта лаба, — остаётся этот
                found = [n for p in parts for n in p.labs]
                number = detected or (current if current and (not found or current in found) else None)
            old_names = shown_names(self.source_names(st))
            new_names = shown_names(sorted(f.name for f in staged.iterdir()))
            date = st.spec.date if st.spec else today()
            if st.spec is not None:
                st.wishes = st.spec.wishes or ""  # пожелания могли поправить на вкладке разбора
            aside = d / "source.old"
            if had_report:
                vid = push_version(st, "до замены методички")
                st.versions[-1].spec.source_text = st.spec.source_text
                aside = d / f"source.v{vid}"  # «Вернуть как было» вернёт и эти файлы
                st.chat.append(ChatMessage(
                    id=uuid.uuid4().hex[:8], role="assistant", at=_now(), version=vid,
                    text=f"Методичка заменена: было {', '.join(old_names) or '—'}, стало {', '.join(new_names)}. "
                         "Задания разобраны заново — выполни лабу ещё раз. Прежний отчёт сохранён, вернуть его "
                         "можно кнопкой ниже."))
                st.report = st.docx_sha = st.docx_path = st.pdf_path = st.engine = None
                st.pages, st.warnings = [], []
            shutil.rmtree(aside, ignore_errors=True)
            moved = False
            if src.exists():
                os.replace(src, aside)
                moved = True
            os.replace(staged, src)
            st.source_path = str(src)
            st.spec, st.findings, st.status, st.error = None, [], "draft", None
            try:
                self._save(st)  # без разбора лаба — черновик с новой методичкой: «Разобрать» доделает
            except Exception:
                shutil.rmtree(src, ignore_errors=True)
                if moved:
                    os.replace(aside, src)
                raise
        finally:
            shutil.rmtree(staged, ignore_errors=True)
        if aside.name == "source.old":
            shutil.rmtree(aside, ignore_errors=True)
        self._prune_sources(st)
        self._emit(st, f"Методичка заменена: {', '.join(new_names)}", step="parse")
        st = self.parse(st, date, number=number, renumber=renumber)
        if st.status == "parsed" and not renumber and detected and current and detected != current:
            st.findings.append(Finding(
                stage="spec", rule_id="spec.other_number", severity="warn", location="Методичка",
                message=f"Новая методичка — ЛР {detected}, а эта лаба — ЛР {current}: задания разобраны как ЛР "
                        f"{current}. Если это другая лаба, создай её отдельно."))
            st = self._save(st)
        return st

    def _prune_sources(self, st: LabState) -> None:
        """Прежние методички хранятся, пока жива версия, к которой их можно вернуть."""
        keep = {f"source.v{v.id}" for v in st.versions}
        d = self.lab_dir(st)
        for f in d.glob("source.v*") if d.exists() else []:
            if f.is_dir() and f.name not in keep:
                shutil.rmtree(f, ignore_errors=True)

    def _restore_source(self, st: LabState, version_id: int, current: int | None) -> None:
        """Откат к версии до замены методички: её файлы возвращаются в source/, нынешние — к версии current."""
        d = self.lab_dir(st)
        saved, src = d / f"source.v{version_id}", d / "source"
        if not saved.is_dir():
            return
        if src.exists():
            keep = d / f"source.v{current}" if current is not None else None
            if keep is not None:
                shutil.rmtree(keep, ignore_errors=True)
                os.replace(src, keep)
            else:
                shutil.rmtree(src)
        os.replace(saved, src)
        st.source_path = str(src)

    def confirm(self, st: LabState, spec: LabSpec | None) -> LabState:
        old_tasks = [(t.n, t.text) for t in st.spec.tasks] if st.spec else None
        old_kind, old_spec = work_kind_of(st), st.spec
        if spec is not None:
            st.spec = spec
        if st.spec is None:
            raise PipelineError("Сначала разбери методичку")
        undo_move = None
        if st.spec.work_kind != old_kind:  # папка сдачи нужна только здесь: обычное сохранение от неё не зависит
            undo_move = self._kind_changed(st, self._hand_in_dir_of(st, old_spec))
        keep = [f for f in st.findings if f.stage != "spec" or f.rule_id != "spec.not_verbatim"]
        if not st.report:
            keep = [f for f in keep if f.stage == "spec"]
        st.findings = keep + verify_verbatim(st.spec, _SourceProxy(st.spec.source_text))
        same_tasks = old_tasks == [(t.n, t.text) for t in st.spec.tasks]
        questions_changed = False
        if st.report is not None and [qa.question for qa in st.report.questions] != list(st.spec.questions):
            # прежние ответы — только на вопросы, что остались; новые спросим при «Продолжить»
            known = {qa.question: qa for qa in st.report.questions}
            st.report.questions = [known[q].model_copy(update={"n": i}) for i, q in enumerate(st.spec.questions, 1)
                                   if q in known]
            questions_changed = True
        if st.report is not None and same_tasks and questions_changed:
            st.status, st.error = "failed", "Вопросы изменились — нажми «Продолжить», чтобы ответить на новые."
            st.docx_sha = None
        elif st.report is not None and same_tasks and st.status in ("rendered", "final", "needs_manual"):
            # задания те же (поправили дату, вариант, вопросы): отчёт на месте, документ — пересобрать
            st.status = "needs_manual" if st.status == "needs_manual" else "rendered"
            st.docx_sha = None
        else:
            st.status = "confirmed"
        return self._save_or_undo(st, undo_move)

    def _save_or_undo(self, st: LabState, undo) -> LabState:
        """Сохранение после смены вида работы: не сохранилось (облако отказало, связь пропала) — папка сдачи
        возвращается на место, иначе на диске была бы «ПЗ7» при сохранённой «лабораторной»."""
        try:
            return self._save(st)
        except Exception:
            if undo is not None:
                undo()
            raise

    def _hand_in_dir(self, st: LabState) -> Path:
        from forlabs.pipeline.appshots import hand_in_dir

        return hand_in_dir(self, st)

    def _hand_in_dir_of(self, st: LabState, spec: LabSpec | None) -> Path | None:
        """Папка сдачи, какой она была при разборе spec (до смены вида работы). None — узнать не удалось (профиль
        не прочитался): тогда и переносить нечего."""
        if spec is None:
            return None
        try:
            return self._hand_in_dir(st.model_copy(update={"spec": spec}))
        except Exception as e:  # noqa: BLE001
            logging.getLogger("forlabs").warning("Папка сдачи: %s", e)
            return None

    def _places_text(self, st: LabState) -> str:
        """Где на компьютере студента лежит то, что создал ForLabs, — фактами для чата правок. Пути чат называет
        только отсюда: без них модель либо выдумывала место, либо отвечала, что путь не записан. Папка сдачи
        недоступна (флешку вынули, профиль не прочитался) — пусто: чат работает и без этого блока."""
        try:
            folder = self._hand_in_dir(st)
            report = folder / f"{work_folder(st)}_{safe_name(get_subject(st.subject_id).short_name)}.docx"
            lines = ["Где что лежит на компьютере студента (другого ForLabs не создавал):",
                     f"Папка сдачи этой работы: {folder}",
                     f"Отчёт Word: {report} "
                     + ("(уже лежит)" if report.is_file() else "(появится после кнопки «Сдать»)")]
            project = Path(st.project.folder) if st.project is not None and st.project.folder else None
            if project is not None and project.is_dir():
                lines.append(f"Проект программы: {project} (открывается кнопкой «Папка проекта» слева)")
            elif project is not None:  # лаба пришла с другого компьютера или папку убрали
                lines.append(f"Проект программы: {project} — этой папки сейчас нет на компьютере; она появится, "
                             "когда ForLabs соберёт программу заново.")
            else:
                lines.append("Проекта программы нет.")
            docs = [n for n in st.task_docs if (folder / n).is_file()]
            if docs:
                lines.append("Файлы заданий (их создал ForLabs, в отчёте — снимки их страниц): "
                             + "; ".join(str(folder / n) for n in docs) + ".")
            others = [x for x in _other_files(folder, project, report) if x not in docs]
            lines.append("Другие файлы в папке сдачи: " + ", ".join(others) + "." if others
                         else "Других файлов в папке сдачи нет.")
            return "\n".join(lines)
        except Exception as e:  # noqa: BLE001 — блок справочный: из-за него правка не должна сорваться
            logging.getLogger("forlabs").warning("Где что лежит: %s", e)
            return ("Где что лежит на компьютере студента: сведений нет — папка сдачи сейчас недоступна. Пути не "
                    "называй; скажи, что папка сдачи недоступна, и предложи проверить её в профиле.")

    def _folder_is_ours(self, st: LabState, folder: Path) -> bool:
        """Папка сдачи точно этой работы и только её. По имени об этом судить нельзя: «ЛР7» — общая для двух работ
        с одним номером, а «ПЗ7» может лежать у студента сама по себе. Своя — если ForLabs клал в неё файлы этой
        работы (проект или сданный отчёт); только её — если ни одна другая работа предмета в неё не смотрит."""
        def same(a, b) -> bool:
            return bool(a) and os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))

        mine = same(st.hand_in_dir, folder)
        if not mine and st.project is not None and st.project.folder:  # работы, сделанные до этого поля
            mine = same(Path(st.project.folder).parent, folder)
        if not mine and st.status == "final":
            short = safe_name(get_subject(st.subject_id).short_name)
            mine = (folder / f"{folder.name}_{short}.docx").is_file()
        if not mine:
            return False
        try:
            for other in self.store.list_labs(st.profile_id, st.subject_id):
                if other.number != st.number and (same(other.hand_in_dir, folder) or work_folder(other) == folder.name):
                    return False
        except Exception:  # noqa: BLE001 — соседей не узнать (нет связи): папку не трогаем
            return False
        return True

    def _kind_changed(self, st: LabState, old_dir: Path | None):
        """Студент поменял вид работы (лабораторная → практическое занятие): выбор запоминается, заголовок отчёта
        меняется при пересборке, папка сдачи переезжает под новое имя («ЛР7» → «ПЗ7») вместе с проектом и сданным
        отчётом — но только своя (см. _folder_is_ours). Занята или открыта — работа остаётся в прежней.
        Возвращает действие «вернуть как было» (если папка переехала) — для отката, когда состояние не сохранилось."""
        kind, chosen, report_kind = st.spec.work_kind, st.work_kind, st.report.work_kind if st.report else None
        st.work_kind = kind
        if st.report is not None:
            st.report.work_kind = kind
        if old_dir is None:
            return None
        try:
            new_dir = self._hand_in_dir(st)
        except Exception as e:  # noqa: BLE001 — профиль не прочитался: вид сменён, папки остаются как есть
            logging.getLogger("forlabs").warning("Папка сдачи: %s", e)
            return None
        if old_dir == new_dir or not old_dir.is_dir() or not self._folder_is_ours(st, old_dir):
            return None
        try:
            if new_dir.exists():
                raise FileExistsError(new_dir)
            old_dir.rename(new_dir)
        except OSError:
            self._emit(st, f"Папка сдачи теперь «{new_dir.name}», но прежняя «{old_dir.name}» осталась на месте: "
                           f"папка «{new_dir.name}» уже есть или файлы в «{old_dir.name}» открыты в другой программе. "
                           "Новые файлы появятся в новой папке — свои файлы перенеси из прежней сам.", "warn")
            return None
        from forlabs.core.fs import atomic_write_text

        manifest = self.lab_dir(st) / "project" / "hand_in.json"
        short = safe_name(get_subject(st.subject_id).short_name)
        before = (st.project.folder if st.project is not None else None, st.hand_in_dir)

        def moved(path: str | None, src: Path, dst: Path) -> str | None:
            try:
                return str(dst / Path(path).relative_to(src)) if path else path
            except ValueError:
                return path

        def point(src: Path, dst: Path) -> None:
            """Всё, что помнит о папке, — на новое место: проект, список своих файлов, сданный отчёт."""
            if st.project is not None:
                st.project.folder = moved(st.project.folder, src, dst)
            st.hand_in_dir = str(dst)
            try:  # список своих файлов привязан к папке: иначе ForLabs принял бы свой проект за файлы студента
                data = json.loads(manifest.read_text(encoding="utf-8"))
                data["folder"] = moved(data["folder"], src, dst)
                atomic_write_text(manifest, json.dumps(data, ensure_ascii=False))
            except (OSError, ValueError, KeyError, TypeError):
                pass
            try:  # и список своих файлов заданий
                files = self.lab_dir(st) / "files" / "hand_in.json"
                data = json.loads(files.read_text(encoding="utf-8"))
                data["folder"] = str(dst)
                atomic_write_text(files, json.dumps(data, ensure_ascii=False))
            except (OSError, ValueError, KeyError, TypeError):
                pass
            try:  # сданный отчёт называется по папке: два отчёта с разными именами рядом — повод сдать не тот
                report, renamed = dst / f"{src.name}_{short}.docx", dst / f"{dst.name}_{short}.docx"
                if report.is_file() and not renamed.exists():
                    report.rename(renamed)
            except OSError:
                pass

        point(old_dir, new_dir)
        handed = (new_dir / f"{new_dir.name}_{short}.docx").is_file()
        self._emit(st, f"Папка сдачи переименована: «{old_dir.name}» → «{new_dir.name}»."
                       + (" Отчёт в ней пока с прежним заголовком — нажми «Сдать», чтобы положить новый."
                          if handed else ""))

        def undo() -> None:
            try:
                if not old_dir.exists():
                    new_dir.rename(old_dir)
                    point(new_dir, old_dir)
            except OSError:
                pass
            if st.project is not None:
                st.project.folder = before[0]
            st.hand_in_dir, st.work_kind = before[1], chosen
            if st.report is not None and report_kind is not None:
                st.report.work_kind = report_kind

        return undo

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

    def set_links(self, st: LabState, numbers: list[str], report: tuple[str, bytes] | None = None
                  ) -> tuple[LabState, list[str]]:
        """Связанные прошлые лабы: номера и (по желанию) общий отчёт. Из отчёта берутся только эти лабы: сначала по
        заголовкам — без модели; чего так не нашлось (заголовок забыт или другой) — модель ищет по скелету отчёта.
        Возвращает номера, которых нет ни в ForLabs, ни в отчёте. Нечитаемый отчёт — предупреждение в журнале."""
        from forlabs.pipeline.memory import LINKED_MAX, split_labs

        with self.lock(st):
            numbers = list(dict.fromkeys(n for n in numbers if n))[:5]
            st.linked = numbers
            auto = self._auto_refs(st)
            prev = self._previous_number(st) if "previous" in auto else None
            wanted = list(dict.fromkeys(numbers + [r for r in auto if r != "previous"] + ([prev] if prev else [])))
            src = self._report_source(st, report) if report is not None else None
            text = src.text if src is not None else ""
            if text.strip() and not wanted:  # отчёт есть, а что из него взять — не сказано: молча он бы пропал
                why = ("а методичка на них не ссылается" if st.spec is not None
                       else "а методичка ещё не разобрана")
                self._emit(st, f"Общий отчёт прочитан, но брать из него нечего: номера прошлых лаб не указаны, {why}. "
                               "Открой слева «Файлы, своя программа, прошлые лабы», впиши номера и прикрепи отчёт "
                               "ещё раз.", "warn")
            if text:
                parts = split_labs(text)
                chosen: dict[str, str] = {}
                for n in wanted:
                    if n in parts:
                        chosen[n] = parts[n]
                        st.linked_how.pop(n, None)
                lost = [n for n in wanted if n not in parts and not self._done_in_forlabs(st, n)]
                for n, (body, how) in self._locate_in_report(st, text, lost).items():
                    chosen[n] = body
                    st.linked_how[n] = how
                # работа вставлена в отчёт снимками документов — текст с них читает модель, и только у нужных работ
                for n, body in self._read_report_pictures(st, src, chosen).items():
                    st.linked_texts[n] = body[:LINKED_MAX]
            st.linked_texts = {n: t for n, t in st.linked_texts.items() if n in wanted}
            st.linked_how = {n: h for n, h in st.linked_how.items() if n in st.linked_texts}
            if st.spec is not None:  # после разбора замечания о связях обновляются сразу
                st.findings = [f for f in st.findings if not f.rule_id.startswith("spec.linked")]
                st.findings += self._link_findings(st)
            st = self._save(st)
        _, missing = self._linked_labs(st)
        return st, [_WORK_PREFIX.sub("", m) for m in missing]

    def _report_source(self, st: LabState, report: tuple[str, bytes]) -> SourceText | None:
        """Общий отчёт: текст с метками на местах картинок и сами картинки (их читает модель — только у нужных
        работ). None — отчёт не прочитался или в нём нет ни текста, ни картинок; причина — в журнале."""
        from forlabs.ingest.extract import PICTURE_RE

        name, data = report
        tmp = self.lab_dir(st) / "linked" / safe_file_name(Path(name or "").name or "report.docx")
        try:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            src = extract_text(tmp, self.tesseract, pictures=True)
        except Exception as e:  # битый или не тот файл — понятная причина в журнале, номера сохраняются
            self._emit(st, f"Общий отчёт «{name}» не прочитался ({getattr(e, 'message_ru', None) or e}) — нужен DOCX "
                           "или PDF с текстом. Номера связанных лаб сохранены.", "warn")
            return None
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)
        if not PICTURE_RE.sub("", src.text).strip():
            self._emit(st, f"В общем отчёте «{name}» нет текста (похоже на скан) — сохрани его из Word как DOCX "
                           "или PDF с текстом.", "warn")
            return None
        return src

    def _read_report_pictures(self, st: LabState, src: SourceText, chosen: dict[str, str]) -> dict[str, str]:
        """Снимки внутри выбранных работ общего отчёта (задания, вставленные снимками документов) читает модель;
        прочитанное встаёт на место снимка. Снимки остальных работ не читаются. Сбой чтения — не ошибка: остаётся
        текст работы, а студенту сказано, чего не хватает."""
        from forlabs.ingest.extract import PICTURE_RE

        from forlabs.pipeline.memory import LINKED_MAX, LINKED_TEXT_PER_PICTURE

        def by_pictures(body: str) -> bool:
            """Работа дана снимками: своего текста на снимок мало. В обычном отчёте (страницы текста и окна
            программы) читать снимки незачем — это минуты ожидания и токены при каждом сохранении связей."""
            marks = len(PICTURE_RE.findall(body))
            return bool(marks) and len(PICTURE_RE.sub("", body)) / marks < LINKED_TEXT_PER_PICTURE

        want = sorted({int(m.group(1)) - 1 for body in chosen.values() if by_pictures(body)
                       for m in PICTURE_RE.finditer(body)})
        want = [i for i in want if 0 <= i < len(src.pictures)]
        texts: dict[int, str | None] = {}
        if want:
            self._emit(st, f"Читаю снимки в общем отчёте: {len(want)}", step="parse")
            try:
                got, error = read_source_pictures([src.pictures[i] for i in want], self._llm(st), {})
            except Exception as e:  # noqa: BLE001 — модель без зрения, сеть, лимит
                got, error = [], str(getattr(e, "message_ru", None) or e)
            texts = dict(zip(want, got))
            unread = sum(1 for i in want if texts.get(i) is None)
            if unread:
                self._emit(st, f"Не прочитал снимков в общем отчёте: {unread} из {len(want)}"
                               + (f" ({error})" if error else "") + ". Что на них важно для этой работы, опиши в "
                               "пожеланиях или прикрепи сами документы в «Файлы к лабе».", "warn", step="parse")

        def filled(body: str) -> str:
            # свой текст работы важнее текста со снимков (слайды, тест на 20 вопросов — это страницы): он остаётся
            # целиком, а снимки делят оставшийся объём поровну; места совсем нет — текст снимков не берётся
            marks = [m for m in PICTURE_RE.finditer(body) if (texts.get(int(m.group(1)) - 1) or "").strip()]
            own = len(PICTURE_RE.sub("", body))
            each = min(2500, (LINKED_MAX - own - 40 * len(marks)) // max(len(marks), 1))
            if each < 200:
                return re.sub(r"\n{3,}", "\n\n", PICTURE_RE.sub("", body)).strip()

            def one(m: re.Match) -> str:
                got = (texts.get(int(m.group(1)) - 1) or "").strip()
                if len(got) > each:
                    got = got[:each].rstrip() + "…"
                return f"[На снимке в отчёте:\n{got}\n]" if got else ""

            return re.sub(r"\n{3,}", "\n\n", PICTURE_RE.sub(one, body)).strip()

        return {n: filled(body) for n, body in chosen.items()}

    def _lab_for_ref(self, st: LabState, ref: str) -> LabState | None:
        """Работа этого предмета с номером ref. Номера у лабораторных и практических свои, поэтому берётся работа
        того же вида, что эта (для ПЗ 5 «работа 4» — это ПЗ 4, даже если она записана как «4-пз»). Работа другого
        вида годится, только если номер вписал сам студент: ссылка методички «в ПЗ№4» — не про ЛР 4."""
        from forlabs.report.derive import key_suffix

        kind = work_kind_of(st)
        found: list[LabState] = []
        for key in dict.fromkeys([ref, f"{ref}-{key_suffix(kind)}"]):
            if key == st.number:
                continue
            try:
                lab = self.store.load_lab(st.profile_id, st.subject_id, key)
            except Exception:  # noqa: BLE001 — нет связи с облаком: работа считается ненайденной
                lab = None
            if lab is not None:
                found.append(lab)
        same = next((x for x in found if work_kind_of(x) == kind), None)
        return same or (found[0] if found and ref in st.linked else None)

    def _done_in_forlabs(self, st: LabState, n: str) -> bool:
        lab = self._lab_for_ref(st, n)
        return lab is not None and lab.report is not None

    def _locate_in_report(self, st: LabState, text: str, wanted: list[str]) -> dict[str, tuple[str, str]]:
        """Модель ищет лабы, у которых в отчёте нет понятного заголовка: по номеру в любом виде, по порядку работ
        или по смыслу (ближе всего к теме текущей лабы). Сбой модели — не ошибка: лаба считается ненайденной."""
        from forlabs.pipeline.linked import locate_labs

        if not wanted:
            return {}
        self._emit(st, f"Ищу в общем отчёте {', '.join('ЛР ' + n for n in wanted)}: заголовков с такими номерами "
                       "нет — смотрит модель (по скелету отчёта, не целиком)")
        try:
            return locate_labs(text, wanted, self._links_context(st), self._llm(st))
        except Exception as e:
            self._emit(st, f"Модель не смогла поискать в отчёте ({getattr(e, 'message_ru', None) or e}) — сохрани "
                           "номера ещё раз позже или добавь заголовки «Лабораторная работа №N» в отчёт.", "warn")
            return {}

    def _links_context(self, st: LabState) -> str:
        """О чём текущая лаба — чтобы модель выбрала в отчёте ближайшую по смыслу часть."""
        if st.spec is not None:
            tasks = "\n".join(t.text for t in st.spec.tasks)
            return f"Тема: {st.spec.topic}\nЦель: {st.spec.goal}\nЗадания:\n{tasks}"[:2500]
        src = self.lab_dir(st) / "source"
        files = sorted(f for f in src.iterdir() if f.is_file() and f.suffix.lower() in {".pdf", ".docx", ".txt", ".md"}
                       ) if src.exists() else []
        text = ""
        if files:
            try:
                text = extract_many(files).text
            except Exception:
                text = ""
        return f"{st.wishes}\n{text}".strip()[:2500]

    def add_materials(self, st: LabState, files: list[tuple[str, bytes]]) -> tuple[LabState, list[str]]:
        """Файлы к лабе: текст каждого — целиком в модель. Что не влезает в лимит или не читается — не добавляется,
        причина возвращается списком (лаба при этом сохраняется с остальными файлами)."""
        from forlabs.pipeline.materials import read_material

        problems: list[str] = []
        with self.lock(st):
            tmp_dir = self.lab_dir(st) / "materials_tmp"
            for name, data in files:
                name = Path(name or "").name or "файл"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                tmp = tmp_dir / safe_file_name(name)
                if isinstance(data, Path):
                    shutil.copyfile(data, tmp)
                else:
                    tmp.write_bytes(data)
                if name.lower().endswith(".zip"):
                    self._add_archive(st, name, tmp, problems)
                    tmp.unlink(missing_ok=True)
                    continue
                try:
                    text = read_material(tmp, self.tesseract)
                except ValueError as e:
                    problems.append(f"«{name}» не добавлен: {e}.")
                    continue
                except Exception as e:  # битый файл не роняет добавление остальных
                    problems.append(f"«{name}» не прочитался ({type(e).__name__}) — пересохрани его или вставь "
                                    "нужное в пожелания.")
                    continue
                finally:
                    tmp.unlink(missing_ok=True)
                if not text.strip():
                    problems.append(f"В «{name}» не нашлось текста — если это скан, сфотографируй страницы "
                                    "и прикрепи их к пожеланиям.")
                    continue
                self._put_material(st, name, text, problems)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            st = self._save(st)
        return st, problems

    def _add_archive(self, st: LabState, name: str, path: Path, problems: list[str], skip_docs: bool = False) -> bool:
        """ZIP — в обычную папку лабы (assets_in/<имя>): код, тексты и таблицы читаются (одним материалом), картинки и
        прочие файлы остаются ресурсами — ForLabs кладёт их в папку проекта программы. skip_docs — документы из
        архива уже стали методичкой."""
        from forlabs.ingest.archive import unpack
        from forlabs.ingest.uploads import DOC_EXT
        from forlabs.pipeline.materials import MATERIALS_MAX, RAW_EXT, read_material

        lab = self.lab_dir(st)
        folder = lab / "assets_in" / safe_file_name(Path(name).stem)
        shutil.rmtree(folder, ignore_errors=True)
        try:
            files = unpack(path, folder)
        except ValueError as e:
            problems.append(f"«{name}» не добавлен: {e}.")
            return False
        readable = RAW_EXT | {".xlsx", ".pdf", ".docx", ".rtf"}
        texts, assets, listing = [], [], []
        for f in sorted(files):
            rel = f.relative_to(folder).as_posix()
            ext = f.suffix.lower()
            listing.append(f"- {rel} ({max(1, f.stat().st_size // 1024)} КБ)")
            if skip_docs and ext in DOC_EXT:
                continue
            if ext in readable:
                try:
                    text = read_material(f, self.tesseract)
                except Exception:  # noqa: BLE001 — нечитаемый файл остаётся в списке
                    text = ""
                if text.strip():
                    texts.append((rel, text))
                    continue
            assets.append(f.relative_to(lab).as_posix())
        if not files:
            problems.append(f"«{name}» не добавлен: архив пустой.")
            return False
        head = f"Архив «{name}» распакован, в нём {len(files)} файл(ов):\n" + "\n".join(listing)
        if assets:
            head += ("\nКартинки и другие файлы из архива ForLabs кладёт в папку проекта программы (по тем же "
                     "путям); в коде ссылайся на них по этим именам.")
        used = sum(len(m.text) for m in st.materials if m.name != name)
        body, cut = [], False
        for rel, text in texts:
            part = f"\n\nФайл {rel}:\n<<<\n{text}\n>>>"
            if used + len(head) + sum(map(len, body)) + len(part) > MATERIALS_MAX:
                cut = True
                continue
            body.append(part)
        if cut:
            problems.append(f"Из «{name}» прочитаны не все файлы: на файлы к лабе есть ограничение объёма. Лишнее "
                            "из архива лучше убрать.")
        if self._put_material(st, name, head + "".join(body), problems, assets=assets):
            return True
        shutil.rmtree(folder, ignore_errors=True)
        return False

    def add_archive(self, st: LabState, name: str, path: Path, skip_docs: bool = False) -> tuple[LabState, list[str]]:
        """Архив к лабе (из поля «Методичка» — документы уже ушли в методичку, skip_docs)."""
        problems: list[str] = []
        with self.lock(st):
            self._add_archive(st, name, Path(path), problems, skip_docs=skip_docs)
            st = self._save(st)
        return st, problems

    @staticmethod
    def _put_material(st: LabState, name: str, text: str, problems: list[str], assets: list[str] | None = None) -> bool:
        """Материал в лабу, если влезает в общий лимит (модель перечитывает материалы в каждом задании)."""
        from forlabs.core.models import Material
        from forlabs.pipeline.materials import MATERIALS_MAX, MAX_FILES, PAGE_CHARS, pages

        others = [m for m in st.materials if m.name != name]
        used = sum(len(m.text) for m in others)
        if used + len(text) > MATERIALS_MAX:
            left = max(MATERIALS_MAX - used, 0)
            problems.append(
                f"«{name}» не добавлен: в нём ≈{pages(len(text))} стр. текста, а на все файлы к лабе можно "
                f"до {MATERIALS_MAX // PAGE_CHARS} стр." + (f" (свободно ≈{left // PAGE_CHARS} стр.)" if used else "")
                + ". Оставь только нужные страницы: модель перечитывает файлы в каждом задании, "
                  "и большой файл съест дневной лимит.")
            return False
        if len(others) >= MAX_FILES:
            problems.append(f"«{name}» не добавлен: к лабе можно приложить до {MAX_FILES} файлов и ссылок.")
            return False
        st.materials = [*others, Material(name=name, text=text, assets=assets or [])]
        return True

    def add_link(self, st: LabState, url: str, http=None) -> tuple[LabState, list[str]]:
        """Страница по ссылке — материал лабы (текст, поля форм, кнопки, ссылки): модель читает её как методичку."""
        from forlabs.pipeline import weblink

        try:
            name, text = weblink.fetch(url, http=http, tesseract=self.tesseract)
        except weblink.LinkError as e:
            return st, [f"Ссылка не добавлена: {e}."]
        problems: list[str] = []
        with self.lock(st):
            self._put_material(st, name, text, problems)
            st = self._save(st)
        return st, problems

    def remove_material(self, st: LabState, name: str) -> LabState:
        with self.lock(st):
            gone = [m for m in st.materials if m.name == name]
            st.materials = [m for m in st.materials if m.name != name]
            for m in gone:
                if m.assets:  # распакованный архив — вместе с материалом
                    shutil.rmtree(self.lab_dir(st) / "assets_in" / safe_file_name(Path(m.name).stem),
                                  ignore_errors=True)
            return self._save(st)

    def set_own(self, st: LabState, src: Path, name: str) -> tuple[LabState, list[str]]:
        """«Своя программа»: папка или ZIP студента → копия в лабе (own/). Отчёт и проект дальше — по ней.
        → (лаба, что не так — словами для студента)."""
        from forlabs.ingest.own import read_own
        from forlabs.pipeline import project as proj

        name = Path(name or Path(src).name).name or "программа"
        with self.lock(st):
            try:
                own = read_own(Path(src), self.lab_dir(st) / "own", name)
            except ValueError as e:
                return st, [f"Программа «{name}» не добавлена: {e}."]
            except OSError as e:
                return st, [f"Программа «{name}» не добавлена: не прочиталась ({e.strerror or e}). Закрой её в среде "
                            "разработки и добавь снова."]
            except Exception as e:  # noqa: BLE001 — странный архив и т. п.: лаба остаётся, студент видит причину
                logging.getLogger("forlabs").warning("своя программа: %s", e, exc_info=True)
                return st, [f"Программа «{name}» не добавлена ({type(e).__name__}). Пересохрани её обычным ZIP или "
                            "выбери папку."]
            if own.engine is None:
                own.engine = proj.engine_in_text(own.text)  # скрипты Unity и без папок проекта
            st.own = own
            st = self._save(st)
        return st, []

    def remove_own(self, st: LabState) -> LabState:
        with self.lock(st):
            shutil.rmtree(self.lab_dir(st) / "own", ignore_errors=True)
            st.own = None
            return self._save(st)

    def _own_text(self, st: LabState) -> str:
        if st.own is None or not st.own.text:
            return ""
        return ("Программа студента — она уже написана, отчёт пишется по ней. Листинги — только её код, дословно (файл "
                "целиком или нужный фрагмент, ничего не меняя и не дописывая). Что программа делает, как работает и что "
                "выводит — описывай по её коду; другой программы не придумывай.\n" + st.own.text)

    def _materials_text(self, st: LabState) -> str:
        if not st.materials:
            return ""
        files = "\n\n".join(f"Файл «{m.name}»:\n<<<\n{m.text}\n>>>" for m in st.materials)
        return ("Файлы к этой лабе от преподавателя (прочитай целиком: данные, имена, значения и текст бери отсюда, "
                f"не придумывай свои):\n{files}")

    def _auto_refs(self, st: LabState) -> list[str]:
        """На какие прошлые лабы ссылается сама эта лаба: своя часть методички, общее задание и задания."""
        from forlabs.pipeline.memory import referenced_labs

        if st.spec is None:
            return []
        refs = referenced_labs(st.spec.source_text or "", st.spec.number, st.spec.work_kind)
        extra = "\n".join([st.spec.assignment or "", *(t.text for t in st.spec.tasks)])
        for r in referenced_labs(extra, st.spec.number, st.spec.work_kind):
            if r not in refs:
                refs.append(r)
        return refs[:3]

    def _previous_number(self, st: LabState) -> str | None:
        """«Предыдущая работа» для ЛР 7 — ЛР 6 (если номер целый)."""
        try:
            f = float((st.spec.number if st.spec else st.number).split("-")[0])
        except ValueError:
            return None
        return str(int(f) - 1) if f == int(f) and f >= 2 else None

    def _project(self, st: LabState, llm, plan=None, retake: bool = False, fix: str = "", previous=None) -> LabState:
        """Этап «Проект»: программа лабы собирается и запускается, снимки окон — сам ForLabs. Сбой — не ошибка лабы.
        fix — просьба из чата исправить программу: модель чинит файлы прежнего плана, затем обычная сборка.
        previous — прежний план, по которому составляется новый после правки отчёта (имена и сценарии не «плывут»)."""
        from forlabs.pipeline import project as proj

        if self.projects and st.report and plan is None and (engine := proj.engine_of(st)):
            # код для Unity, Android Studio, Arduino: чужой проект вместо него не строим — причина остаётся у рисунков
            if proj.mark_engine(st, engine):
                self._emit(st, f"Проект {engine} ForLabs не собирает и не запускает: код — в листингах отчёта, "
                               "снимки сделай сам", "warn", step="check")
            return st
        if not self.projects or not st.report or (plan is None and not proj.needs_project(st)):
            return st
        emit = lambda text, kind="info": self._emit(st, text, kind, step="check")  # noqa: E731
        own = proj.own_sources(self, st)
        if st.own is not None and own is None and not st.own.engine and st.own.language:
            emit("Файлов твоей программы нет на этом компьютере — добавь её заново на странице лабы", "warn")
        outcome = ""  # чем кончилась просьба исправить: applied, rejected (не сложилось в план), broke (не собралось)
        why = ""  # почему не вышло — словами для студента
        before, was_built = None, st.project is not None and st.project.built
        try:
            if plan is None and fix and own:
                # программа студента: план — из его файлов, просьба — исправлением поверх них (иначе файлы
                # студента молча заменили бы то, что модель написала по просьбе)
                emit("Составляю проект программы")
                plan = proj.plan_project(st, llm, self._memory_text(st, own=False), previous=previous, own=own)
                if found := proj.validate(plan):
                    if not all(x.startswith("Снимок ") for x in found):
                        raise PipelineError("план проекта не прошёл проверку: " + "; ".join(found[:3]))
                    plan = proj.drop_bad_shots(plan)
            if plan is not None and fix:
                emit("Исправляю программу")
                log = st.project.build_log if st.project else ""
                fixed = proj.repair_project(plan, fix, log, llm, st)
                if problems := proj.validate(fixed):  # не сложилось в план: модель узнаёт почему и пробует ещё раз
                    again = f"{fix}\nПрошлое исправление не принято: " + "; ".join(problems[:5])
                    fixed = proj.repair_project(plan, again, log, llm, st)
                    problems = proj.validate(fixed)
                if problems:
                    emit("Исправление не принято: " + "; ".join(problems[:3]), "warn")
                    outcome, why = "rejected", "; ".join(problems[:2])
                elif fixed == plan:
                    outcome, why = "rejected", "модель не предложила изменений в файлах"
                else:
                    before, plan, outcome = plan, fixed, "applied"
            if plan is None:
                emit("Составляю проект программы")
                plan = proj.plan_project(st, llm, self._memory_text(st, own=False), wish=fix, previous=previous, own=own)
                outcome = "applied" if fix else ""
                if st.project is not None and st.project.name and not (st.own and st.own.project_name):
                    # план составлен заново: проект остаётся в своей папке (у программы студента — его имя)
                    plan = plan.model_copy(update={"name": st.project.name})
                def checked(candidate):
                    found = proj.validate(candidate)
                    if found and all(p.startswith("Снимок ") for p in found):
                        return proj.drop_bad_shots(candidate), []
                    return candidate, found

                plan, problems = checked(plan)
                if problems:  # модель узнаёт, что не так, и составляет план ещё раз
                    emit("План проекта не прошёл проверку — составляю заново", "warn")
                    plan = proj.plan_project(st, llm, self._memory_text(st, own=False), wish=fix,
                                             rejected="; ".join(problems[:5]), previous=previous, own=own)
                    if st.project is not None and st.project.name and not (st.own and st.own.project_name):
                        plan = plan.model_copy(update={"name": st.project.name})
                    plan, problems = checked(plan)
                if problems:
                    raise PipelineError("план проекта не прошёл проверку: " + "; ".join(problems[:3]))
            st = proj.build_and_shoot(self, st, plan, llm, emit, retake=retake)
            if before is not None and was_built and not st.project.built:
                # исправление сломало рабочую программу: студенту возвращается прежняя, а не та, что не собирается
                emit("Исправление не собралось — возвращаю прежнюю программу", "warn")
                st = proj.build_and_shoot(self, st, before, None, emit, retake=retake)
                outcome = "broke"
            if own and (final := proj.load_plan(self, st)) is not None and (changed := proj.own_changed(final, own)):
                emit("Чтобы твоя программа собралась, ForLabs поправил: " + ", ".join(changed[:6])
                     + ". Посмотри эти файлы в папке сдачи", "warn")
            left = [f for f in self.slots(st)]
            sql = st.project.language == "mysql"
            if st.project.built:
                head = ("Скрипты SQL проверены" if sql else "Программа собрана") + f". Снял сам: {self._auto_shots(st)}"
            elif st.project.missing:
                head = "Программу не собирал: не хватает " + ", ".join(
                    proj._tool_title(k) for k in st.project.missing)
            elif st.project.blocked:
                head = "Программу не собирал и не запускал: " + st.project.blocked[0].rstrip(".")
            else:
                head = "Скрипты SQL выполняются с ошибкой" if sql else "Программа не собралась: ошибки остались"
            emit(head + (f", нужно от тебя: {len(left)}" if left else ""), "done" if st.project.built else "warn")
        except Exception as e:  # проект — помощь, а не условие: лаба собирается и без него
            logging.getLogger("forlabs").warning("Этап «Проект»: %s", e, exc_info=True)
            reason = str(getattr(e, "message_ru", None) or e)
            emit(f"Программу собрать не получилось ({reason}). Снимки сделай сам.", "warn")
            proj.mark_unmade(st, reason)
            outcome, why = ("rejected", reason) if fix else ("", "")
        self.fix_outcome[self.key(st)] = outcome
        self.fix_reason[self.key(st)] = why
        return self._save(st)

    def _plan_app_shots(self, st: LabState, llm) -> LabState:
        """Какие рисунки ForLabs снимет сам в программах студента. Сбой — не ошибка лабы."""
        from forlabs.pipeline import appshots

        if not self.agents or not st.report or not appshots.pending(st):
            return st
        emit = lambda text, kind="info": self._emit(st, text, kind, step="check")  # noqa: E731
        try:
            st = appshots.plan(self, st, llm, emit)
        except Exception as e:  # noqa: BLE001
            logging.getLogger("forlabs").warning("план снимков в программах: %s", e, exc_info=True)
        return self._save(st)

    def app_shots(self, st: LabState) -> LabState:
        """«Сделать снимки в программах»: агент открывает программы и снимает рисунки с app_task."""
        from forlabs.pipeline import appshots

        emit = lambda text, kind="info": self._emit(st, text, kind, step="check")  # noqa: E731
        with self.lock(st):
            before, after = self.app_hooks
            try:
                st = appshots.run(self, st, self._llm(st), emit, before=before, after=after)
            finally:  # что бы ни случилось, уже снятые рисунки остаются в лабе
                st = self._save(st)
        return self.render(st)

    @staticmethod
    def _auto_shots(st: LabState) -> int:
        return sum(1 for s in st.report.sections for b in s.blocks
                   if isinstance(b, Figure) and (b.image_path or "").startswith("assets/shot_"))

    def retry_project(self, st: LabState) -> LabState:
        """«Повторить» после установки программы: тот же план, сборка и снимки заново (модель — только на исправления)."""
        from forlabs.pipeline import project as proj

        with self.lock(st):
            if st.project is None and proj.load_plan(self, st) is None:
                raise PipelineError("Проекта ещё нет — выполни лабу.")
            projects, self.projects = self.projects, True
            try:
                # студент мог поправить код у себя в папке проекта: пересборка берёт его текст, а не затирает его
                proj.import_student_edits(self, st, lambda t, kind="info": self._emit(st, t, kind, step="check"))
                st = self._reproject(st, self._llm(st), retake=True)
            finally:
                self.projects = projects
        return self.render(st)

    def _reproject(self, st: LabState, llm, fresh: bool = False, retake: bool = False, fix: str = "") -> LabState:
        """Программа и снимки заново. Прежний план годится, только если отчёт с тех пор не меняли: план ссылается на
        блоки по номерам, и после правки кода в чате он вернул бы в отчёт старый код. Иначе (и при fresh — код
        только что меняли) план составляется снова."""
        from forlabs.pipeline import project as proj

        old = proj.load_plan(self, st)
        moved = proj.own_moved(st)  # своя программа добавлена, заменена или убрана: прежний проект — не её
        plan = None if fresh or moved else old
        if plan is not None and proj.stale(st, plan):
            plan = None
        previous = old if plan is None and not moved else None
        return self._project(st, llm, plan=plan, retake=retake, fix=fix, previous=previous)

    def _chat_reshoot(self, st: LabState, llm, code_changed: bool = False,
                      force: bool = True, fix: str = "") -> tuple[LabState, str, bool]:
        """Снимки программы по просьбе в чате (force: и те, что ForLabs уже снимал) или после правки кода; fix —
        просьба исправить саму программу: сначала чинятся файлы проекта, потом сборка и снимки.
        → (лаба, что получилось — строками для ответа, менялось ли что-то: тогда документ собирается заново)."""
        from forlabs.pipeline import project as proj

        def figures() -> dict[tuple[int, int], Figure]:
            return {(sec.n, i): b for sec in st.report.sections for i, b in enumerate(sec.blocks, start=1)
                    if isinstance(b, Figure)}

        if not self.projects:
            return st, "", False
        if st.project is None and not proj.needs_project(st):
            return st, "\nВ этой лабе нет программы, которую ForLabs мог бы запустить сам, — скриншоты добавь ты.", False
        was = {k: b.image_path for k, b in figures().items()}
        auto = {k for k, path in was.items() if path and Path(path).name.startswith("shot_")}  # снятые ForLabs
        missing = {k for k, path in was.items() if not path}
        outdated = code_changed or proj.stale(st, proj.load_plan(self, st))
        if not fix and not missing and not outdated and not (force and auto):
            return st, "\nВсе рисунки уже на месте.", False
        st = self._reproject(st, llm, fresh=code_changed, retake=force or outdated or bool(fix), fix=fix)
        now = figures()
        taken = [b.topic for k, b in now.items() if b.image_path and b.image_path != was.get(k)]
        same = [now[k].topic for k in sorted(auto) if k in now and now[k].image_path == was[k]]
        left = [b for b in now.values() if not b.image_path]
        lines = []
        built = st.project is not None and st.project.built
        outcome = self.fix_outcome.get(self.key(st), "") if fix else ""
        if outcome == "applied" and built:
            lines.append("Программу исправил и собрал заново.")
        elif outcome == "broke":
            lines.append("Исправление не собралось, поэтому я вернул прежнюю программу. Напиши точнее, что поменять.")
        elif fix and outcome != "applied":
            why = self.fix_reason.get(self.key(st), "").strip().rstrip(".") or "правка не сложилась в рабочий проект"
            lines.append(f"Исправить не получилось: {why}. Попробуй ещё раз или напиши точнее, что поменять.")
        elif outdated and built:
            lines.append("Программу пересобрал под новый код.")
        if st.project is not None and st.project.missing:  # чего нет, зачем оно и как поставить — со ссылкой
            from forlabs.runner import tools as tool_catalog

            for key in st.project.missing[:3]:
                lines.append("Не хватает программы " + (tool_catalog.how_to_get(key) or key))
            left = [b for b in left if not b.need_tool or b.need_tool.startswith("app:")]  # причина уже названа
        wanted = list(dict.fromkeys(b.need_tool[4:] for b in left if (b.need_tool or "").startswith("app:")))
        if wanted:  # снимки в программах студента (Visual Studio и т.п.), которых на компьютере нет
            from forlabs.agent import apps as agent_apps

            lines += ["Для снимка нужна программа " + agent_apps.how_to_get(key) for key in wanted[:3]]
            left = [b for b in left if not (b.need_tool or "").startswith("app:")]
        if st.project is not None and not built and st.project.build_log.strip():
            # не «ошибки остались», а сама ошибка компилятора: по ней видно, что чинить
            lines.append(f"Сборка не прошла: {proj.first_error(st.project.build_log)}")
            lines.append("Все ошибки — слева, в блоке «Программа». Напиши «исправь ошибки сборки», и я попробую ещё раз.")
            left = [b for b in left if "не собралась" not in (b.auto_note or "")]  # причина уже названа
        if taken:
            lines.append("Снял сам: " + "; ".join(f"«{t}»" for t in taken[:6]) + ".")
        if same and (force or outdated):
            lines.append("Не переснял: " + "; ".join(f"«{t}»" for t in same[:5]) + " — оставил прежний снимок.")
        in_apps = [b for b in left if b.app_task is not None]  # их снимает не пересборка, а работа в программе
        if in_apps:
            lines.append("Эти рисунки снимаются в самих программах — нажми «Сделать снимки в программах» в блоке "
                         "«Снимки в программах» слева: " + "; ".join(f"«{b.topic}»" for b in in_apps[:5]) + ".")
        for b in [x for x in left if x.app_task is None][:5]:
            why = (b.auto_note or "").removeprefix("Сам не снял: ").rstrip(".")
            lines.append(f"Не получилось: «{b.topic}»" + (f" — {why}." if why else "."))
        return st, "".join("\n" + ln for ln in lines), True

    def _linked_labs(self, st: LabState) -> tuple[list[tuple[str, str, str, str]], list[str]]:
        """Прошлые лабы для этой: выбранные студентом и те, на которые ссылается методичка. Каждая —
        (номер, откуда, тема, текст, подпись): сделанная в ForLabs — целиком с кодом, иначе кусок прикреплённого
        отчёта. Подпись — как работа называется на самом деле («ЛР 4», даже если ссылается на неё ПЗ)."""
        from forlabs.pipeline.memory import LINKED_MAX
        from forlabs.pipeline.write import section_text

        from forlabs.report.derive import lab_label

        current = st.spec.number if st.spec else st.number
        refs = list(dict.fromkeys(st.linked + self._auto_refs(st)))
        if not refs:
            return [], []

        def num(n: str) -> float:
            try:
                return float(n.split("-")[0])
            except ValueError:
                return -1.0

        found: list[tuple[str, str, str, str, str]] = []
        missing: list[str] = []
        others: list[LabState] | None = None
        for ref in refs[:5]:
            lab, key = None, ref
            if ref == "previous":
                if others is None:
                    try:  # «предыдущая работа» — своего вида: у ПЗ 3 это ПЗ 2, а не ЛР 2
                        others = [x for x in self.store.list_labs(st.profile_id, st.subject_id)
                                  if x.number != st.number and x.report is not None
                                  and work_kind_of(x) == work_kind_of(st)]
                    except Exception:
                        others = []
                earlier = [x for x in others if 0 <= num(x.number) < num(current)]
                lab = max(earlier, key=lambda x: num(x.number)) if earlier else None
                prev = self._previous_number(st)
                if prev and prev in st.linked_texts and (lab is None or num(lab.number) < num(prev)):
                    lab, key = None, prev  # в отчёте есть ровно предыдущая — она точнее более ранней из ForLabs
                elif lab is not None:
                    key = lab.number
                label = "предыдущей лабы"
            else:
                lab = self._lab_for_ref(st, ref)
                label = lab_label(work_kind_of(st), ref)
            if any(f[0] == key for f in found):
                continue
            if lab is not None and lab.report is not None:
                body = "\n\n".join(f"Задание {x.n}: {x.title}\n{section_text(x)}" for x in lab.report.sections)
                variant = f" {lab.spec.variant_text}" if lab.spec and lab.spec.variant_text else ""
                found.append((lab.number, "forlabs", lab.spec.topic if lab.spec else "",
                              (variant + "\n" + body)[:LINKED_MAX], work_label(lab)))
            elif key in st.linked_texts:
                found.append((key, "report", "", st.linked_texts[key], lab_label(work_kind_of(st), key)))
            else:
                missing.append(label)
        return found, missing

    def _link_findings(self, st: LabState) -> list:
        from forlabs.core.models import Finding

        found, missing = self._linked_labs(st)
        def where(n: str, src: str, text: str) -> str:
            if src == "forlabs":
                return ": из ForLabs, с кодом."
            how = st.linked_how.get(n)
            if how:
                first = " ".join(text.split())[:90]
                return (f": заголовка в отчёте не было, модель нашла её {how}. Начинается с «{first}». "
                        "Если не та, поправь номер.")
            return ": из прикреплённого отчёта."

        out = [Finding(stage="spec", rule_id="spec.linked_lab", severity="warn", location="Связь с прошлыми лабами",
                       message=label + (f" «{topic}»" if topic else "") + where(n, src, text))
               for n, src, topic, text, label in found]
        # студент уже описал прошлую лабу в пожеланиях (как и просит замечание) — второй раз не просим
        from forlabs.pipeline.memory import _LAB_REF, _PRACTICE_PREV, _PRACTICE_REF, _PREV_REF, norm_number

        wishes = (st.wishes or (st.spec.wishes if st.spec else "") or "")
        told = {norm_number(m.group(1)) for ref in (_LAB_REF, _PRACTICE_REF) for m in ref.finditer(wishes)}
        about_previous = bool(_PREV_REF.search(wishes) or _PRACTICE_PREV.search(wishes)) or bool(told)
        missing = [m for m in missing
                   if not (norm_number(_WORK_PREFIX.sub("", m).strip()) in told if _WORK_PREFIX.match(m)
                           else about_previous)]
        out += [Finding(stage="spec", rule_id="spec.linked_lab_missing", severity="warn",
                        location="Связь с прошлыми лабами",
                        message=f"Нет {m} ни в ForLabs, ни в отчёте: прикрепи общий отчёт с этой работой или "
                                "опиши её в пожеланиях.")
                for m in missing]
        return out

    def _linked_text(self, st: LabState) -> str:
        from forlabs.pipeline.memory import LINKED_TOTAL

        found, _ = self._linked_labs(st)
        cut = LINKED_TOTAL // max(len(found), 1)
        return "\n\n".join(label.replace(" ", " №", 1) + (f" «{topic}»" if topic else "") + f"\n{text[:cut]}"
                            for _, _, topic, text, label in found)

    def _memory_text(self, st: LabState, own: bool = True) -> str:
        """own=False — без программы студента (план проекта получает её целиком отдельно)."""
        text = ""
        if self.memory is not None:
            try:
                text = self.memory.read(st.profile_id, st.subject_id)
            except Exception:
                text = ""
        if text.strip():
            text = f"Прошлые лабы этого предмета (продолжай ту же тему):\n{text.strip()}"
        linked = self._linked_text(st)
        if linked:  # методичка прямо ссылается на прошлые работы — они целиком (с кодом)
            text = (f"Работы, на которые ссылается методичка (продолжай их: та же тема, имена, код):\n{linked}\n\n"
                    f"{text}").strip()
        theme = ""
        if self.memory is not None:
            try:
                theme = self.memory.theme(st.profile_id, st.subject_id)
            except Exception:
                theme = ""
        if theme:
            text = f"Тематика предмета (сквозная, держись её во всех лабах): {theme}\n\n{text}".strip()
        teacher = self._teacher_text(st)
        if teacher:
            text = f"Требования преподавателя (соблюдай всегда):\n{teacher}\n\n{text}".strip()
        materials = self._materials_text(st)
        if materials:
            text = f"{materials}\n\n{text}".strip()
        program = self._own_text(st) if own else ""
        if program:
            text = f"{program}\n\n{text}".strip()
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
        squares = bool(SQUARES_RE.search(message))  # «убери квадраты / невидимые символы» — это не текст отчёта
        with self.lock(st):
            msg_id = uuid.uuid4().hex[:8]
            rels = self._save_images(st, images, f"chat_{msg_id}") if images else []
            user_msg = ChatMessage(id=msg_id, role="user", text=message[:4000], at=_now(), remember=remember,
                                   images=rels)
            st.chat.append(user_msg)
            self._save(st)
            from forlabs.pipeline import project as proj
            from forlabs.pipeline.lookup import ProjectView

            # правки студента в папке проекта — уже часть лабы: чат видит и правит его код, а не прежний
            mine = (proj.import_student_edits(self, st, lambda t, kind="info": self._emit(st, t, kind, step="write"))
                    if self.projects else [])
            if mine:
                self._save(st)
            backup_spec, backup_report = st.spec.model_copy(deep=True), st.report.model_copy(deep=True)
            backup_kind, undo_move, layout_changed, files_changed = st.work_kind, None, False, False
            vid = None
            try:
                profile = self._profile(st)
                subject = get_subject(st.subject_id)
                llm = self._llm(st, chat=msg_id)
                if rels:
                    self._emit(st, "Читаю приложенные фото", step="write")
                    user_msg.image_text = describe_images([self.lab_dir(st) / r for r in rels], llm)
                    self._save(st)
                    message = f"{message}\n\nНа приложенных фото:\n{user_msg.image_text}".strip()
                self._emit(st, "Разбираю правку", step="write")
                enrollment = self._enrollment(profile, st.subject_id)
                view = ProjectView(plan=proj.load_plan(self, st), st=st,
                                   folder=st.project.folder if st.project is not None else None)
                plan = plan_edit(st, message, profile, subject, llm, self.rules, self._teacher_text(st), enrollment,
                                 view=view, places=self._places_text(st))
                from forlabs.pipeline.chat import profile_changes

                proposals = profile_changes(plan.profile, profile, enrollment)
                rule = (plan.remember or "").strip() if remember and self.memory is not None else ""
                note = ""
                if remember and self.memory is not None and not rule:
                    note = ("\nНе понял, что запомнить на будущее — напиши требование коротко, например "
                            "«комментарии к каждому методу», и отметь «Запомнить».")

                def remember_rule() -> str:
                    if not rule:
                        return note
                    try:  # не запомнилось (нет связи) — удачная правка от этого не откатывается
                        self.memory.add_teacher(st.profile_id, st.subject_id, rule)
                    except Exception as e:
                        logging.getLogger("forlabs").warning("Требование не запомнено: %s", e)
                        return (f"\nНе получилось запомнить требование ({getattr(e, 'message_ru', e)}) — отметь "
                                "«Запомнить» ещё раз, когда будет связь.")
                    return f"\nЗапомнил для следующих лаб: {rule.rstrip('.')}."

                if plan.project and st.project is not None and st.project.missing:
                    # программы нет на компьютере: код тут ни при чём — проверим ещё раз (вдруг уже поставили)
                    plan.project, plan.reshoot = None, True
                was_final = st.status == "final"
                if plan.kind != "edit":
                    shots, touched = "", False
                    if plan.project and self.projects:  # «исправь программу»: чинит и листинги отчёта — с откатом
                        vid = push_version(st, message)
                    if plan.reshoot or plan.project or mine:  # «сними сам ещё раз», «исправь ошибки сборки»
                        # и когда студент сам поправил код: программа и снимки должны быть от его текста
                        st, shots, touched = self._chat_reshoot(st, llm, fix=plan.project or "")
                        if touched and was_final:
                            shots += "\nЛаба уже была сдана. Сдай заново, чтобы в папке сдачи был новый файл."
                    applied = self.fix_outcome.pop(self.key(st), "") == "applied"
                    if vid is not None and not (touched and applied):  # правка не применилась: откатывать нечего
                        st.versions, vid = [v for v in st.versions if v.id != vid], None
                    if mine:  # листинги отчёта теперь из его файлов: документ собирается заново
                        touched = True
                        shots += "\nВзял твои правки из папки проекта: " + ", ".join(mine[:6]) + "."
                    if squares:  # квадраты снимает сборка документа — собираем заново и проверяем результат
                        touched = True
                    note = remember_rule()
                    reply = ChatMessage(id=uuid.uuid4().hex[:8], role="assistant",
                                        text=(plan.reply + shots + note).strip(), at=_now(), kind=plan.kind,
                                        profile=proposals, version=vid)
                    st.chat.append(reply)
                    self._emit(st, "Ответ в чате правок", "done", step="write")
                    if not touched:  # ничего не менялось — документ остаётся как был (сданная лаба — сданной)
                        return self._save(st)
                if plan.kind == "edit":
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
                    if plan.work_kind and plan.work_kind != st.spec.work_kind:
                        from forlabs.report.derive import work_title

                        kind_dir = self._hand_in_dir_of(st, st.spec)
                        st.spec.work_kind = plan.work_kind
                        undo_move = self._kind_changed(st, kind_dir)
                        changed.append(f"вид работы — {work_title(plan.work_kind).lower()}")
                    if plan.task_files is not None:
                        st.spec.task_files = plan.task_files
                        files_changed = True
                        changed.append("задания — отдельными файлами" if plan.task_files
                                       else "задания — текстом в отчёте")
                    if plan.layout:
                        st.spec.plain_layout = plan.layout == "plain"
                        layout_changed = True
                        changed.append("вёрстка — без разрывов страниц и продолжений" if st.spec.plain_layout
                                       else "вёрстка — по образцу")
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
                    self._emit(st, "Проверяю текст", step="check")
                    # чиним только то, что правили, и не трогаем принятые студентом замечания
                    touched = {f"Задание {e.n}," for e in plan.tasks} | {f"Вопрос {q.n}" for q in plan.questions}
                    accepted = set(st.accepted_rules)
                    report, findings = check_and_repair(
                        st.report, st.spec, profile, llm, self.rules, self._others(st),
                        on_event=lambda t: self._emit(st, t, step="check"),
                        scope=lambda f: any(f.location.startswith(t) for t in touched) and not is_accepted(f, accepted))
                    st.report = _relative_images(report, self.lab_dir(st))
                    st.findings = _keep_spec(st.findings, findings)
                    self._sync_assets(st)
                    shots = ""
                    codes = lambda r: [b.code for sec in r.sections for b in sec.blocks  # noqa: E731
                                       if isinstance(b, Listing)]
                    code_changed = codes(st.report) != codes(backup_report)
                    if self.projects and (plan.reshoot or plan.project or mine
                                          or ((retake or code_changed) and st.project is not None)):
                        # код отчёта изменился: программа в папке сдачи и снимки должны быть от нового кода
                        st, shots, _ = self._chat_reshoot(st, llm, code_changed, force=plan.reshoot or bool(mine),
                                                          fix=plan.project or "")
                        self.fix_outcome.pop(self.key(st), None)
                        open_topics = {b.topic for sec in st.report.sections for b in sec.blocks
                                       if isinstance(b, Figure) and not b.image_path}
                        retake = [t for t in retake if t in open_topics]
                    text = plan.reply or "Готово."
                    if changed:
                        text += "\nИзменено: " + ", ".join(changed) + "."
                    text += shots
                    if retake and not shots:
                        text += "\nПересними: " + "; ".join(retake) + " — места появились в «Нужно от тебя»."
                    elif retake:
                        text += "\nЭти рисунки сними сам — места в «Нужно от тебя»."
                    if was_final:
                        text += "\nЛаба уже была сдана. Сдай заново, чтобы в папке сдачи был новый файл."
                    note = remember_rule()
                    reply = ChatMessage(id=uuid.uuid4().hex[:8], role="assistant", text=text + note, at=_now(),
                                        kind="edit", changed=changed, retake=retake, version=vid, profile=proposals)
                    st.chat.append(reply)
            except Exception as e:  # сбой посреди правки: разбор и отчёт — как до неё
                st.spec, st.report, st.work_kind = backup_spec, backup_report, backup_kind
                if undo_move is not None:  # вид работы успел поменяться и папка сдачи переехала — назад
                    undo_move()
                    undo_move = None
                if vid is not None:
                    st.versions = [v for v in st.versions if v.id != vid]
                reason = getattr(e, "message_ru", None) or str(e) or type(e).__name__
                st.chat.append(ChatMessage(id=uuid.uuid4().hex[:8], role="assistant", at=_now(), kind="failed",
                                           text=f"Не получилось: {reason} Отчёт не изменён — попробуй ещё раз."))
                self._emit(st, f"Правка не удалась: {reason}", "error", step="write")
                return self._save(st)
            st.docx_sha = None  # документ устарел, пока вёрстка не закончится
            self._save_or_undo(st, undo_move)  # правка сохранена до вёрстки: выключение её не потеряет
        st = self.render(st)
        if squares:
            reply.text += "\n" + self._squares_note(st)
        if layout_changed:
            reply.text += "\n" + self._layout_note(st)
        if files_changed:
            reply.text += "\n" + self._files_note(st)
        if st.status == "failed":
            reply.text += (f"\nПравку сохранил, но документ не собрался: {st.error} "
                           "Когда исправишь, нажми «Пересобрать документ».")
        if squares or layout_changed or files_changed or st.status == "failed":
            st = self._save(st)
        return st

    # ---------- задания отдельными файлами ----------

    def _doc_pages(self, docx: Path, work: Path, author: str) -> list[Path]:
        """Картинки страниц документа задания. Пусто — снять нечем (нет ни Word, ни LibreOffice)."""
        if self.page_renderer is not None:
            return list(self.page_renderer(docx, work))
        rep = finalize(docx, author, work / "render", self.soffice, prefer_word=self.prefer_word, plain=True)
        return list(rep.pages)

    def _task_files(self, st: LabState, profile: Profile, d: Path) -> ReportDoc:
        """Режим «задания отдельными файлами»: документ Word на каждое готовое задание и на ответы — в папку сдачи,
        снимки их страниц — в отчёт. Возвращает отчёт для вёрстки (исходный отчёт лабы не меняется). Документ
        пересобирается, только если его содержимое изменилось: каждая сборка — запуск Word."""
        import base64
        import hashlib

        from forlabs.pipeline import taskfiles as tf
        from forlabs.render.docx_builder import build_answers_doc, build_task_doc

        files_dir = d / "files"
        if st.spec is None or not st.spec.task_files or st.report is None:
            self._hand_in_task_files(st, {}, files_dir)  # режим выключен: свои файлы из папки сдачи убираются
            return st.report
        try:
            stamp = base64.b64decode(profile.stamp) if profile.stamp else None
        except ValueError:
            stamp = None
        author = profile.doc_author or profile.full_name
        try:
            index = json.loads((files_dir / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            index = {}
        full = _absolute_images(st.report, d)
        units: list[tuple[object, str, object]] = [
            (sec.n, tf.file_name(sec), sec) for sec in full.sections
            if tf.ready(sec) and not tf.is_presentation(sec.title)]
        if full.questions and all(q.answer.strip() for q in full.questions):
            units.append((tf.ANSWERS, tf.ANSWERS_TITLE + ".docx", full.questions))
        shots: dict = {}
        docs: dict[str, Path] = {}
        fresh: dict[str, dict] = {}
        for key, name, item in units:
            body = json.dumps([x.model_dump() for x in item] if isinstance(item, list) else item.model_dump(),
                              ensure_ascii=False, sort_keys=True)
            sha = hashlib.sha256((body + name + author + (profile.stamp or "")).encode("utf-8")).hexdigest()[:16]
            work = files_dir / str(key)
            target = work / name
            was = index.get(str(key)) or {}
            if was.get("sha") == sha and target.is_file() and all(Path(p).is_file() for p, _ in was.get("shots", [])):
                made = [(str(p), label) for p, label in was.get("shots", [])]
            else:
                self._emit(st, f"Собираю файл: {name}", step="render")
                shutil.rmtree(work, ignore_errors=True)
                work.mkdir(parents=True, exist_ok=True)
                if isinstance(item, list):
                    build_answers_doc(item, target, author, stamp)
                else:
                    build_task_doc(item, target, author, stamp)
                pages = self._doc_pages(target, work, author)
                made = [(str(p), label) for p, label in tf.sheets(pages, work / "shots", "s")] if pages else []
            fresh[str(key)] = {"sha": sha, "name": name, "shots": made}
            docs[name] = target
            if made:
                shots[key] = [(Path(p), label) for p, label in made]
        for old in set(index) - set(fresh):  # задания больше нет или оно снова ждёт снимка — его файл не нужен
            shutil.rmtree(files_dir / old, ignore_errors=True)
        try:
            files_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(files_dir / "index.json", json.dumps(fresh, ensure_ascii=False).encode("utf-8"))
        except OSError:
            pass
        self._hand_in_task_files(st, docs, files_dir)
        if docs and not shots:
            self._emit(st, "Файлы заданий лежат в папке сдачи, но снимки их страниц вставить не получилось: для этого "
                           "нужен Word или LibreOffice. В отчёте пока текст заданий.", "warn", step="render")
        return tf.as_screens(st.report, shots)

    def _shown_report(self, st: LabState, d: Path) -> ReportDoc:
        """Отчёт, как он свёрстан: в режиме файлов — со снимками уже собранных документов (ничего не пересобирая)."""
        from forlabs.pipeline import taskfiles as tf

        if st.spec is None or not st.spec.task_files or st.report is None:
            return st.report
        try:
            index = json.loads((d / "files" / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return st.report
        shots = {}
        for key, info in index.items():
            made = [(Path(p), label) for p, label in info.get("shots", []) if Path(p).is_file()]
            if made:
                shots[tf.ANSWERS if key == tf.ANSWERS else int(key)] = made
        return tf.as_screens(st.report, shots)

    def _hand_in_task_files(self, st: LabState, docs: dict[str, Path], files_dir: Path) -> None:
        """Файлы заданий — в папку сдачи. Меняются только свои файлы; свой прежний, которого больше нет, убирается.
        Файл, который студент поправил сам (или положил свой с тем же именем), перед заменой сохраняется рядом
        как «имя.bak»; открытый в Word — пропускается с замечанием."""
        import hashlib

        manifest = files_dir / "hand_in.json"
        try:
            before = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            before = {}
        if not docs and not before.get("files"):
            st.task_docs = []
            return
        try:
            folder = self._hand_in_dir(st)
        except Exception as e:  # noqa: BLE001 — папка сдачи недоступна: файлы остаются в лабе, отчёт собирается
            self._emit(st, f"Файлы заданий не положены в папку сдачи ({getattr(e, 'message_ru', None) or e}).", "warn")
            return

        def sha_of(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()[:16]

        mine = dict(before.get("files") or {}) if before.get("folder") == str(folder) else {}
        written: dict[str, str] = {}
        locked: list[str] = []
        kept: list[str] = []
        for name, src in docs.items():
            dst = folder / name
            try:
                new = sha_of(src)
                if dst.is_file():
                    cur = sha_of(dst)
                    if cur == new:
                        written[name] = new
                        continue
                    if mine.get(name) != cur:  # не тот файл, что клал ForLabs: правка студента или его собственный
                        shutil.copy2(dst, dst.with_name(dst.name + ".bak"))
                        kept.append(name)
                folder.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:6]}.tmp")
                shutil.copy2(src, tmp)
                os.replace(tmp, dst)
                written[name] = new
            except OSError:
                locked.append(name)
                if name in mine:
                    written[name] = mine[name]
        for name in set(mine) - set(docs):
            try:
                gone = folder / name
                if gone.is_file() and sha_of(gone) == mine[name]:  # свой и не менялся — можно убрать
                    gone.unlink()
            except OSError:
                pass
        try:
            files_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(manifest, json.dumps({"folder": str(folder), "files": written},
                                                    ensure_ascii=False).encode("utf-8"))
        except OSError:
            pass
        st.task_docs = sorted(written)
        if written:
            st.hand_in_dir = str(folder)  # папка сдачи теперь точно этой работы: в ней её файлы заданий
        if kept:
            self._emit(st, "Эти файлы заданий ты менял сам, а ForLabs собрал их заново. Твои версии лежат рядом с "
                           "пометкой .bak: " + ", ".join(kept[:6]), "warn", step="render")
        if locked:
            self._emit(st, "Не получилось обновить файлы заданий — они открыты в Word: " + ", ".join(locked[:6])
                           + ". Закрой их и нажми «Пересобрать документ».", "warn", step="render")

    def _files_note(self, st: LabState) -> str:
        """Что на самом деле получилось с файлами заданий — по папке сдачи, а не со слов модели."""
        if not st.spec.task_files:
            return "Пересобрал отчёт: текст заданий снова в нём, своих файлов заданий в папке сдачи не оставил."
        if st.status == "failed":
            return "Файлы заданий появятся, когда документ соберётся."
        try:
            folder = self._hand_in_dir(st)
        except Exception:  # noqa: BLE001
            folder = None
        made = [n for n in st.task_docs if folder is not None and (folder / n).is_file()]
        waiting = [s.n for s in st.report.sections
                   if not any(n.startswith(f"Задание {s.n}") for n in made)]
        out = (f"Файлы заданий лежат в папке {folder}: " + ", ".join(made) + "." if made
               else "Файлов заданий пока нет.")
        if waiting:
            out += (" Без файла остались задания " + ", ".join(map(str, waiting)) + ": в них есть снимки, которые "
                    "делаешь ты, или это презентация — её ForLabs не делает.")
        return out

    def _layout_note(self, st: LabState) -> str:
        """Что на самом деле с разрывами после сборки — по самому документу, а не со слов модели."""
        docx = self.lab_dir(st) / "report.docx"
        if st.status == "failed" or not docx.exists():
            return "Вёрстка поменяется, когда документ соберётся."
        if not st.spec.plain_layout:
            return "Пересобрал документ по образцу: длинные таблицы и листинги снова переносятся с «Продолжение …»."
        try:
            import zipfile

            with zipfile.ZipFile(docx) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            left = xml.count('w:type="page"') + len(re.findall(r">Продолжение (?:таблицы|листинга)\b", xml))
        except Exception:  # noqa: BLE001
            left = -1
        if left == 0:
            return ("Пересобрал документ: разрывов страниц и «Продолжение таблицы» в нём нет, таблицы и листинги "
                    "идут целиком. Если Word ещё показывает старый файл, закрой его и открой заново.")
        return ("Убрать разрывы не получилось — это ошибка ForLabs. Напиши в «Поддержку», приложив этот отчёт.")

    def _squares_note(self, st: LabState) -> str:
        """Что на самом деле с квадратами после сборки — по самому документу, а не со слов модели."""
        docx = self.lab_dir(st) / "report.docx"
        what = ("Чёрные квадраты слева — это не символы текста, а отметки настроек абзаца в Word («не отрывать "
                "от следующего», «не разрывать абзац»).")
        if st.status == "failed" or not docx.exists():
            return what + " Они уберутся, когда документ соберётся."
        try:
            left = freeze.flags_left(docx)
        except Exception:  # noqa: BLE001
            left = -1
        if left == 0:
            return (what + " Пересобрал документ — в нём их не осталось. Если Word ещё показывает старый файл, "
                           "закрой его и открой заново.")
        return (what + " Убрать их не получилось — это ошибка ForLabs. Напиши в «Поддержку», приложив этот "
                       "отчёт.")

    def apply_profile_change(self, st: LabState, msg_id: str, apply: bool) -> LabState:
        """Ответ на предложение чата поменять данные профиля: применить (профиль, затем пересборка этой лабы;
        другие лабы предмета покажут «Документ нужно пересобрать») или отказаться."""
        with self.lock(st):
            msg = next((m for m in st.chat if m.id == msg_id and m.profile), None)
            if msg is None or msg.profile_state:
                raise PipelineError("Это предложение уже обработано")
            if not apply:
                msg.profile_state = "declined"
                return self._save(st)
            profile = self._profile(st)
            enrollment = self._enrollment(profile, st.subject_id)
            for change in msg.profile:
                value: object = int(change.value) if change.field in ("variant", "workplace") else change.value
                if change.field in ("full_name", "title_name", "gender", "group"):
                    setattr(profile, change.field, value)
                else:
                    setattr(enrollment, change.field, value)
            if profile.enrollment(st.subject_id) is None:
                profile.enrollments.append(enrollment)
            self.store.save_profile(profile)
            msg.profile_state = "applied"
            self._save(st)
        if st.report is not None and st.status in ("rendered", "needs_manual", "final"):
            st = self.render(st)
        return st

    def revert(self, st: LabState, version_id: int) -> LabState:
        """Вернуть разбор и отчёт к версии; текущее состояние тоже сохраняется версией — откат можно отменить."""
        target = next((v for v in st.versions if v.id == version_id), None)
        if target is None:
            raise PipelineError("Этой версии уже нет: хранятся только 10 последних правок")
        with self.lock(st):
            # после замены методички отчёта может ещё не быть — тогда сохранять нечего
            current = push_version(st, f"до отката к «{target.label}»") if st.report and st.spec else None
            source = st.spec.source_text if st.spec else ""
            was_kind, was_spec = work_kind_of(st), st.spec
            st.spec, st.report = target.spec.model_copy(deep=True), target.report.model_copy(deep=True)
            undo_move = None
            if st.spec.work_kind != was_kind:  # версия с другим видом работы: папка сдачи возвращается вместе с ним
                undo_move = self._kind_changed(st, self._hand_in_dir_of(st, was_spec))
            text = f"Вернул отчёт к состоянию до правки «{target.label[:80]}»."
            if target.spec.source_text:  # версия до замены методички: возвращается и методичка
                if current is not None:
                    st.versions[-1].spec.source_text = source
                self._restore_source(st, version_id, current)
                st.findings = verify_verbatim(st.spec, _SourceProxy(st.spec.source_text)) + self._link_findings(st)
                text = "Вернул прежнюю методичку и отчёт по ней."
            else:
                st.spec.source_text = source  # в версиях методичка не хранится (облако: меньше данных)
            self._prune_sources(st)
            st.chat.append(ChatMessage(id=uuid.uuid4().hex[:8], role="assistant", at=_now(), kind="revert",
                                       version=current, text=text))
            st.docx_sha = None
            self._save_or_undo(st, undo_move)
        return self.render(st)

    def execute(self, st: LabState, only: list[int] | None = None, full: bool = False) -> LabState:
        """full — «Выполнить заново»: написать всё с чистого листа, даже если прошлый запуск упал на дописанной лабе."""
        with self.lock(st):
            try:
                profile = self._profile(st)
                subject = get_subject(st.subject_id)
                enrollment = self._enrollment(profile, st.subject_id)
                if not st.spec.tasks:
                    raise PipelineError("В разборе нет заданий — добавь их на вкладке «Разбор методички».")
                if not full and only is None and st.status in ("failed", "executing") and st.report is not None:
                    # прошлый запуск оборвался: дописываем только то, чего ещё нет (пустые задания — заново)
                    done = {s.n for s in st.report.sections if section_has_content(s)}
                    only = [t.n for t in st.spec.tasks if t.n not in done]
                    if done:
                        self._emit(st, f"Продолжаю: уже написаны задания {', '.join(map(str, sorted(done)))}",
                                   step="write")
                elif only is None and st.report is not None:
                    # «Выполнить заново»: прежний отчёт — в версии, новый пишется с чистого листа (если запуск
                    # оборвётся, «Продолжить» допишет новый, а не выдаст старый за готовый)
                    push_version(st, "до выполнения заново")
                    st.report, st.docx_sha = None, None
                elif only is not None and st.report is not None:
                    push_version(st, f"до переписывания задания {', '.join(map(str, only))}")
                st.status, st.error = "executing", None
                st.docx_sha = None  # пока задания переписываются, прежний документ не выдаётся за готовый
                # связанные лабы могли появиться в ForLabs после разбора — замечания о них по текущему состоянию
                st.findings = [f for f in st.findings if not f.rule_id.startswith("spec.linked")]
                st.findings += self._link_findings(st)
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
                self._emit(st, "Проверяю текст", step="check")
                report, findings = check_and_repair(report, st.spec, profile, llm, self.rules, self._others(st),
                                                    on_event=lambda t: self._emit(st, t, step="check"))
                st.report = _relative_images(report, self.lab_dir(st))
                st.findings = _keep_spec(st.findings, findings)
                self._sync_assets(st)
                left = [f for f in findings if f.severity == "error"]
                self._emit(st, "Проверка: замечаний нет" if not left else f"Осталось замечаний: {len(left)}",
                           "done" if not left else "warn", step="check")
                st.docx_sha = None
                self._save(st)  # отчёт целиком — до вёрстки (Word может работать минуты)
                st = self._project(st, llm)
                st = self._plan_app_shots(st, llm)
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
        try:
            import io as _io

            from PIL import Image as _Img

            with _Img.open(_io.BytesIO(image)) as im:
                if im.size[1] > im.size[0] * 3:
                    self._emit(st, "Снимок очень высокий — в отчёте он будет узкой полосой. Лучше снять только нужную "
                                   "часть окна или разделить на два снимка.", "warn")
        except Exception:
            pass
        rel = f"assets/fig_{section}_{block}_{uuid.uuid4().hex[:6]}{suffix}"  # новый файл: старые снимки целы
        f = self.lab_dir(st) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(image)
        old_rel, b.image_path = b.image_path, rel
        self._sync(st, [rel])
        self._emit(st, f"Скриншот для задания {section} добавлен")
        st.docx_sha = None
        self._save(st)  # скриншот сохранён до вёрстки
        self._drop_replaced(st, old_rel)
        return self.render(st)

    def _drop_replaced(self, st: LabState, rel: str | None) -> None:
        """Заменённый снимок удаляется, если к нему не вернуть ни одной версией (иначе облако растёт)."""
        if not rel or Path(rel).is_absolute():
            return
        for v in st.versions:
            if any(getattr(b, "image_path", None) == rel for s in v.report.sections for b in s.blocks):
                return
        try:
            (self.lab_dir(st) / rel).unlink(missing_ok=True)
            if hasattr(self.store, "delete_file"):
                self.store.delete_file(st.profile_id, st.subject_id, st.number, rel)
        except Exception as e:  # не удалилось — не страшно
            logging.getLogger("forlabs").info("Старый снимок %s не удалён: %s", rel, e)

    def clean_marks(self, st: LabState) -> LabState:
        """Документ, свёрстанный версией до 1.0.17, ещё хранит настройки «не отрывать от следующего» и т. п. —
        в Word это чёрные квадраты слева. Перед сдачей, скачиванием и открытием в Word они снимаются на месте
        (разбивка от этого не меняется), документ остаётся свежим. Файл занят Word — ничего не делаем."""
        d = self.lab_dir(st)
        docx = d / "report.docx"
        if not docx.exists() or not docx_fresh(st, d):
            return st
        try:
            if not freeze.flags_left(docx):
                return st
            freeze.apply(docx)
        except Exception as e:  # noqa: BLE001 — файл открыт в Word или повреждён: остаётся как был
            logging.getLogger("forlabs").warning("Квадраты не сняты: %s", e)
            return st
        st.docx_sha = file_sha(docx)
        self._sync(st, ["report.docx"])
        return self._save(st)

    def render(self, st: LabState) -> LabState:
        try:
            if st.spec is not None and not lab_complete(st):
                raise PipelineError("Отчёт дописан не до конца — нажми «Продолжить», чтобы дописать задания и ответы.")
            profile = self._profile(st)
            if st.spec is not None:
                # профиль или разбор могли поменяться после выполнения: шапка, вывод и проверки — по текущим
                st.report = refresh_derived(st.report, st.spec, profile, self._enrollment(profile, st.subject_id))
                st.findings = _keep_spec(st.findings, check_all(st.report, st.spec, profile, self._others(st)))
            self._emit(st, "Верстаю документ", step="render")
            d = self.lab_dir(st)
            self._retry_pending(st)
            self._fetch_missing(st)
            self._drop_broken_images(st)
            docx = d / "report.docx"
            shown = self._task_files(st, profile, d)  # в режиме файлов: отчёт со снимками документов заданий
            try:
                build_lab(_absolute_images(shown, d), docx, author=profile.doc_author or profile.full_name)
            except PermissionError as e:
                raise PipelineError("report.docx открыт в Word или другой программе — закрой его и нажми "
                                    "«Пересобрать документ».") from e
            self._emit(st, "Собираю документ и предпросмотр", step="finalize")
            rep = finalize(docx, profile.doc_author or profile.full_name, d / "render", self.soffice,
                           prefer_word=self.prefer_word, plain=bool(st.spec is not None and st.spec.plain_layout))
            # последний шлюз: готовый документ (после доводки) сверяется с эталоном абзац за абзацем
            canon = compare(docx)
            for f in canon:
                # шрифт и его размер одинаковы у всех ролей текста — их расхождение настоящее (держит сдачу);
                # остальное (отступы, интервалы) сверка выводит из роли абзаца, угаданной по тексту, и ошибается
                # на обычных фразах («… на рисунке 1», «Цель:») — показываем, но сдачу не держим
                if f.location == "continuation" or not _font_mismatch(f.message):
                    f.severity = "warn"
            st.findings = [f for f in st.findings if f.rule_id != "render.canon_mismatch"] + canon
            if canon:
                self._emit(st, f"Оформление отличается от эталона в {len(canon)} местах — смотри замечания", "warn",
                           step="render")
            st.docx_path, st.pdf_path = str(docx), (str(rep.pdf) if rep.pdf else None)
            st.docx_sha = file_sha(docx)
            st.render_inputs = render_inputs(profile, self._enrollment(profile, st.subject_id))
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
        if st.spec is not None and not lab_complete(st):
            raise PipelineError("Отчёт дописан не до конца — нажми «Продолжить».")
        d = self.lab_dir(st)
        docx = d / "report.docx"
        if st.docx_sha and docx.exists() and not docx_fresh(st, d):
            # файл менялся вне ForLabs (открыли в Word и сохранили): не теряем эти правки молча
            aside = d / f"report.manual-{datetime.now():%Y%m%d-%H%M%S}.docx"
            try:
                shutil.copyfile(docx, aside)
                self._emit(st, f"Документ меняли вне ForLabs (в Word) — твой вариант сохранён как {aside.name} в "
                               "папке лабы, в папку сдачи ушёл пересобранный. Правки надёжнее вносить чатом правок.",
                           "warn")
            except OSError:
                pass
        st = self.clean_marks(st)
        profile_now = self._profile(st)
        stale_inputs = st.render_inputs != render_inputs(profile_now, self._enrollment(profile_now, st.subject_id))
        if not docx_fresh(st, d) or has_slots(docx) or stale_inputs:
            # документ устарел: свёрстан на другом компьютере, вёрстку оборвали или до вставки скриншотов
            st = self.render(st)
            if st.status == "failed" or not docx.exists() or has_slots(docx):
                raise PipelineError(st.error or "Не удалось свёрстать отчёт без мест для скриншотов")
        profile = self._profile(st)
        subject = get_subject(st.subject_id)
        root = output_root(profile)
        short = safe_name(subject.short_name)
        out_dir = root / short / work_folder(st)
        target = out_dir / f"{work_folder(st)}_{short}.docx"
        tmp = target.with_name(target.name + ".tmp")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            # сначала копия, потом замена — выключение не оставит обрезанный файл; в копии квадратов нет никогда
            freeze.clean_copy(docx, tmp)
            durable_replace(tmp, target)
        except PermissionError as e:
            tmp.unlink(missing_ok=True)
            raise PipelineError(f"Не получилось записать {target.name}: файл открыт в Word или его держит OneDrive "
                                "либо антивирус. Закрой файл (или подожди синхронизацию OneDrive) и нажми ещё раз.") from e
        except OSError as e:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise PipelineError(f"Папка сдачи недоступна: {root}. Проверь флешку или диск либо выбери другую папку "
                                f"в профиле. ({getattr(e, 'strerror', '') or e})") from e
        st.accepted_rules = sorted(accepted)
        st.status = "final"
        st.hand_in_dir = str(out_dir)  # папка сдачи теперь точно этой работы: в ней её отчёт
        self._emit(st, f"Готово: {target}", "done")
        st = self._save(st)  # сдано — сразу; память предмета обновляется в фоне (запрос к модели может быть долгим)
        if self.memory is not None:
            snapshot, llm = st.model_copy(deep=True), self._llm(st)
            subject_key = f"{st.profile_id}/{st.subject_id}"
            job = f"memory/{self.key(st)}"
            with self._bg_lock:
                self.background.add(job)
                lock = self._memory_locks.setdefault(subject_key, threading.Lock())

            def update_memory() -> None:
                try:
                    with lock:  # две сдачи подряд по одному предмету не теряют друг друга
                        self.memory.update(snapshot, llm)
                except Exception as e:
                    logging.getLogger("forlabs").warning("Память предмета не обновлена: %s", e)
                finally:
                    with self._bg_lock:
                        self.background.discard(job)

            t = threading.Thread(target=update_memory, name="memory-update", daemon=True)
            self._threads = [x for x in self._threads if x.is_alive()] + [t]
            t.start()
        return st

    def build_semester(self, profile_id: str, subject_id: str, numbers: list[str], out: Path) -> tuple[Path, object]:
        profile = self.store.get_profile(profile_id)
        enrollment = self._enrollment(profile, subject_id)
        docs = []
        for n in numbers:
            st = self.store.load_lab(profile_id, subject_id, n)
            if st and st.report:
                if st.spec is not None and not lab_complete(st):
                    raise PipelineError(f"{work_label(st)} дописана не до конца — нажми в ней «Продолжить» или убери "
                                        "её из отчёта")
                if self.slots(st):
                    raise PipelineError(f"В {work_label(st)} не вставлены скриншоты — вставь их или убери её из "
                                        "отчёта")
                self._fetch_missing(st)
                lab_dir = self.store.lab_dir(profile_id, subject_id, n)
                docs.append(_absolute_images(self._shown_report(st, lab_dir), lab_dir))
        if not docs:
            raise PipelineError("Нет готовых лаб для отчёта за семестр")
        out = Path(out)
        build_semester(docs, profile, enrollment, out)
        from forlabs.core import paths

        work = paths.data_home() / "render_semester" / safe_name(profile_id) / safe_name(subject_id)
        rep = finalize(out, profile.doc_author or profile.full_name, work, self.soffice, prefer_word=self.prefer_word)
        return out, rep


LEASE = timedelta(minutes=15)  # вёрстка Word без сохранений длится до 5 минут


def machine_id() -> str:
    """Постоянный номер этого компьютера (папка данных) — чтобы отличать «выполняется здесь» от «на другом»."""
    from forlabs.core import paths
    from forlabs.core.fs import atomic_write_text

    f = paths.data_home() / "machine_id"
    try:
        v = f.read_text(encoding="utf-8").strip()
        if v:
            return v
    except OSError:
        pass
    v = uuid.uuid4().hex
    try:
        atomic_write_text(f, v)
    except OSError:
        pass
    return v


def running_elsewhere(st: LabState) -> bool:
    """Лаба сейчас выполняется на другом компьютере (аренда не истекла)."""
    if st.status != "executing" or not st.lease_machine or st.lease_machine == machine_id():
        return False
    try:
        until, now = datetime.fromisoformat(st.lease_until or ""), datetime.now(timezone.utc)
    except ValueError:
        return False
    # часы того компьютера могут спешить: аренда дальше обычного срока — не верим ей
    return now < until <= now + LEASE + timedelta(minutes=10)


_WORK_PREFIX = re.compile(r"^(?:ЛР|ПР|ПЗ) ")  # «ЛР 7», «ПЗ 2» → номер
_BUILD_DIRS = {"obj", "bin", "out", "__pycache__", ".vs", ".git", ".idea", "node_modules", "venv", ".venv"}


def _other_files(folder: Path, project: Path | None, report: Path, limit: int = 30, look: int = 3000) -> list[str]:
    """Файлы папки сдачи, кроме отчёта и проекта программы: то, что положил студент или сохранила программа.
    Папки сборки не обходятся, обход ограничен: в папке сдачи может лежать что угодно."""
    out: list[str] = []
    seen = 0
    if not folder.is_dir():
        return out
    for root, dirs, files in os.walk(folder):
        here = Path(root)
        dirs[:] = sorted(d for d in dirs if d.lower() not in _BUILD_DIRS and (project is None or here / d != project))
        for name in sorted(files):
            seen += 1
            f = here / name
            if f != report and not name.startswith(("~$", ".")):
                out.append(f.relative_to(folder).as_posix())
            if len(out) >= limit or seen >= look:
                return out
    return out


def work_kind_of(st) -> str:
    """Вид работы: из разбора, до разбора — выбранный студентом, иначе лабораторная. st — лаба или её строка
    в списке (LabSummary: разбора в ней нет, вид записан прямо)."""
    spec = getattr(st, "spec", None)
    return spec.work_kind if spec is not None else (getattr(st, "work_kind", None) or "lab")


def plain_number(number: str) -> str:
    """Номер работы без служебного хвоста вида: «7-пз» → «7», «7-пз-2» (та же работа второй раз) → «7»."""
    return re.sub(r"-(?:лр|пр|пз)(?:-\d+)?$", "", number or "", flags=re.IGNORECASE)


def work_label(st) -> str:
    """Как работа названа в списках и сообщениях: «ЛР 7», «ПЗ 2» (st — лаба или её строка в списке)."""
    from forlabs.report.derive import lab_label

    return lab_label(work_kind_of(st), st.number)


def work_folder(st: LabState) -> str:
    """Имя папки сдачи работы и начало имени её отчёта: «ЛР7», «ПЗ2» — по виду работы и номеру из методички."""
    from forlabs.report.derive import work_short

    number = safe_name(st.spec.number if st.spec else plain_number(st.number))  # из методички, а не «7-2»
    return f"{work_short(work_kind_of(st))}{number}"


def output_root(profile: Profile) -> Path:
    """Папка сдачи профиля: кавычки от «Копировать как путь» убираются, относительный путь — от папки пользователя
    (а не от папки программы, куда в Program Files писать нельзя)."""
    raw = (profile.output_root or "").strip().strip('"').strip("'").strip()
    if not raw:
        return Path.home() / "Лабораторные"
    p = Path(raw).expanduser()
    return p if p.is_absolute() else Path.home() / p


def _font_mismatch(message: str) -> bool:
    import re

    return bool(re.search(r"(^|[;:]\s*)(size|font|sz|bold|italic)\w*:", message))


def _word_image(f: Path) -> bool:
    """Картинка, которую вставит python-docx: PNG, JPEG, GIF, BMP — и не CMYK."""
    try:
        from PIL import Image

        with Image.open(f) as im:
            return im.format in ("PNG", "JPEG", "GIF", "BMP") and im.mode != "CMYK"
    except Exception:
        return False


def _several_labs(text: str, number: str) -> list:
    """Номер не был известен, а в методичке несколько лаб (сборник): модель выбрала одну — студенту видно какую."""
    from forlabs.core.models import Finding
    from forlabs.pipeline.labnum import labs_in_text

    found = labs_in_text(text)
    if len(found) < 2:
        return []
    shown = ", ".join(found[:12]) + (" …" if len(found) > 12 else "")
    return [Finding(stage="spec", rule_id="spec.several_labs", severity="warn", location="Методичка",
                    message=f"В ней несколько лаб: {shown}. Разобрана ЛР {number}. Нужна другая — создай новую лабу "
                            "и впиши её номер в поле «Номер лабы», а эту удали.")]


def _keep_spec(old: list, new: list) -> list:
    """Замечания разбора (вариант по рабочему месту, «не дословно») переживают проверки отчёта."""
    return [f for f in old if f.stage == "spec"] + [f for f in new if f.stage != "spec"]


def render_inputs(profile: Profile, enrollment) -> str:
    """Отпечаток данных профиля, попадающих в документ (группа, ФИО, автор, преподаватель…): поменялись после
    вёрстки — «Сдать» пересоберёт документ."""
    import hashlib

    raw = profile.model_dump_json() + (enrollment.model_dump_json() if enrollment is not None else "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


SQUARES_RE = re.compile(r"квадрат|невидим\w*\s+(?:символ|знак)|непечата\w*|знак\w*\s+форматировани|"
                        r"скрыт\w*\s+символ", re.IGNORECASE)


def file_sha(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def docx_fresh(st: LabState, lab_dir: Path) -> bool:
    """report.docx на этом компьютере — именно тот, что получился при последней удачной вёрстке этой лабы
    (не старый с другого компьютера и не недоделанный при оборванной вёрстке)."""
    docx = Path(lab_dir) / "report.docx"
    try:
        return bool(st.docx_sha) and docx.exists() and file_sha(docx) == st.docx_sha
    except OSError:
        return False


def lab_complete(st: LabState) -> bool:
    """Все задания написаны (не пусты) и на все вопросы методички есть непустые ответы."""
    if not st.spec or not st.report:
        return False
    written = {s.n for s in st.report.sections if section_has_content(s)}
    if any(t.n not in written for t in st.spec.tasks):
        return False
    if [qa.question for qa in st.report.questions] != list(st.spec.questions):
        return False
    return all(qa.answer.strip() for qa in st.report.questions)


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
