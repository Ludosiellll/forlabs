"""Состояние приложения: режим (облако/локально), хранилище, конвейер, фоновые задачи."""

from __future__ import annotations

import re
import threading
import traceback
from typing import Callable

from forlabs.cloud.auth import CloudAuth, CloudError
from forlabs.cloud.config import DEFAULT as CLOUD
from forlabs.cloud.store import CloudStore
from forlabs.core.llm import LLM
from forlabs.core.rules import RuleBook
from forlabs.core.settings import Settings, get_api_key, load_settings
from forlabs.core.store import Store
from forlabs.core.subjects import Subject, add_custom_subject
from forlabs.pipeline.events import EventBus
from forlabs.pipeline.memory import SubjectMemory
from forlabs.pipeline.run import Pipeline

_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def slug(text: str) -> str:
    s = "".join(_TR.get(c, c) for c in text.lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "profile"


class AppContext:
    def __init__(self, settings: Settings | None = None, llm_override: Callable[[], object] | None = None,
                 prefer_word: bool = True, render_diagrams: bool = True, synchronous: bool = False):
        self.settings = settings or load_settings()
        self.llm_override = llm_override
        self.prefer_word = prefer_word
        self.render_diagrams = render_diagrams
        self.synchronous = synchronous
        self.bus = EventBus()
        self.auth = CloudAuth(CLOUD)
        self._store = None
        self._pipeline = None
        self.running: set[str] = set()
        self.form_tokens: dict[str, str] = {}  # одноразовые токены формы «Новая лаба» → адрес созданной лабы
        self._lock = threading.Lock()
        if self.cloud:
            self.auth.load_session()

    @property
    def cloud(self) -> bool:
        return self.settings.mode == "cloud"

    def signed_in(self) -> bool:
        return (not self.cloud) or self.auth.session is not None

    def reset(self) -> None:
        self._store = None
        self._pipeline = None

    @property
    def store(self):
        if self._store is None:
            self._store = CloudStore(self.auth) if self.cloud else Store()
        return self._store

    def sync_subjects(self) -> None:
        """Свои предметы из облака кладутся в локальный список (добавлены на другом компьютере)."""
        if hasattr(self.store, "list_custom_subjects"):
            for subj in self.store.list_custom_subjects():
                add_custom_subject(subj)

    def add_subject(self, subject: Subject) -> None:
        add_custom_subject(subject)
        if hasattr(self.store, "save_custom_subject"):
            self.store.save_custom_subject(subject)

    def rules(self) -> RuleBook:
        if self.cloud:
            st = self.store
            return RuleBook(get_overrides=st.rule_overrides, save=st.save_rule_override, reset=st.reset_rule_override)
        return RuleBook()

    def memory(self) -> SubjectMemory:
        if self.cloud:
            st = self.store
            return SubjectMemory(read_fn=st.read_memory, write_fn=st.write_memory)
        return SubjectMemory()

    def llm(self):
        if self.llm_override:
            return self.llm_override()
        if self.cloud:
            llm = LLM(self.settings, api_key=self.auth.access_token, base_url=CLOUD.llm_base_url)
            llm.accepts_lab_tag = True
            return llm
        key = get_api_key()
        if not key:
            raise CloudError("Не задан ключ API. Открой «Настройки» и вставь ключ.")
        return LLM(self.settings, api_key=key)

    @property
    def pipeline(self) -> Pipeline:
        if self._pipeline is None:
            self._pipeline = Pipeline(self.store, self.llm, rules=self.rules(), bus=self.bus,
                                      render_diagrams=self.render_diagrams, prefer_word=self.prefer_word,
                                      soffice=self.settings.soffice_path, memory=self.memory())
        return self._pipeline

    def claim(self, key: str) -> bool:
        """Занять лабу под короткое действие в запросе (сдача). False — по ней уже идёт работа."""
        with self._lock:
            if key in self.running:
                return False
            self.running.add(key)
            return True

    def release(self, key: str) -> None:
        with self._lock:
            self.running.discard(key)

    def run(self, key: str, fn: Callable, *args, **kwargs) -> None:
        """Длинная операция: в фоне (в тестах — синхронно). Одна операция на лабу одновременно."""
        with self._lock:
            if key in self.running:
                return
            self.running.add(key)

        def job():
            pipe = self._pipeline  # настройки могут сброситься посреди работы — перенос лабы помнит этот конвейер
            try:
                fn(*args, **kwargs)
            except Exception as e:  # ошибка должна дойти до журнала, а не потеряться в потоке
                self.bus.publish(key, f"Ошибка: {getattr(e, 'message_ru', None) or e}", "error")
                traceback.print_exc()
            finally:
                with self._lock:
                    self.running.discard(key)
                self.bus.publish(key, "", "idle")
                moved = pipe.renamed.get(key) if pipe is not None else None
                if moved:  # лабу перенесли под номер из методички — её страница ждёт конца работы по новому ключу
                    self.bus.publish(f"{key.rsplit('/', 1)[0]}/{moved}", "", "idle")

        if self.synchronous:
            job()
        else:
            threading.Thread(target=job, daemon=True).start()
