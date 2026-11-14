"""Прямой доступ к провайдеру модели со своим ключом участника.

Функция llm (`/direct`) выдаёт вошедшему участнику со своим ключом настройку: адрес провайдера, модель и ключ.
Настройка живёт в памяти процесса не дольше ttl: так закончившаяся подписка или заменённый ключ подхватываются
за несколько минут. Расход каждого прямого запроса уходит в функцию (`/usage`) в фоне — для графиков в админке.
Ключ никуда не пишется: ни на диск, ни в журнал, ни в repr.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

log = logging.getLogger("forlabs")


@dataclass
class DirectConfig:
    base_url: str
    model: str
    models: list[str]
    key: str = field(repr=False)
    max_tokens: int = 32000
    timeout_s: float = 600.0


class DirectAccess:
    def __init__(self, http: httpx.Client, function_url: str, token: Callable[[], str], ttl: float = 600.0,
                 clock: Callable[[], float] = time.monotonic):
        self.http = http
        self.function_url = function_url.rstrip("/")
        self.token = token
        self.ttl = ttl
        self.clock = clock
        self._cached: tuple[float, DirectConfig | None] | None = None
        self._down_until = 0.0
        self._lock = threading.Lock()
        self._reports: list[threading.Thread] = []

    def _token(self) -> str:
        from forlabs.core.llm import LLMError

        try:
            return self.token()
        except Exception as e:  # обновление входа не удалось
            if getattr(e, "offline", False):
                raise LLMError("network", str(e), getattr(e, "message_ru", None), retryable=True) from e
            raise LLMError("access", str(e), getattr(e, "message_ru", None) or
                           "Не удалось получить доступ к модели. Войди в аккаунт заново.") from e

    def config(self) -> DirectConfig | None:
        """Настройка прямого доступа или None (нет своего ключа, пробный доступ, путь временно отложен). Подписка
        закончилась, вход не принят — LLMError с тем же текстом, что и на пути через сервер."""
        from forlabs.core.llm import LLMError, error_for

        now = self.clock()
        with self._lock:
            if now < self._down_until:
                return None
            if self._cached is not None and now - self._cached[0] < self.ttl:
                return self._cached[1]
        try:
            r = self.http.post(f"{self.function_url}/direct", json={},
                               headers={"Authorization": f"Bearer {self._token()}"}, timeout=30)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            raise LLMError("network", str(e), retryable=True) from e
        if r.status_code == 404:
            cfg = None
        elif r.status_code >= 500:
            return None  # функция споткнулась — этот запрос пойдёт через неё же обычным путём, без кэша
        elif r.status_code >= 400:
            raise error_for(r.status_code, r.text[:300], r.headers.get("retry-after"))
        else:
            try:
                d = r.json()
                cfg = DirectConfig(base_url=str(d["base_url"]).rstrip("/"), model=str(d["model"]),
                                   models=[str(m) for m in d.get("models") or [d["model"]]], key=str(d["key"]),
                                   max_tokens=int(d.get("max_tokens") or 32000),
                                   timeout_s=float(d.get("timeout_s") or 600))
            except (ValueError, KeyError, TypeError):
                return None
        with self._lock:
            self._cached = (now, cfg)
        return cfg

    def invalidate(self) -> None:
        """Ключ не принят провайдером: настройка запрашивается заново (администратор мог заменить ключ)."""
        with self._lock:
            self._cached = None

    def mark_down(self) -> None:
        """Провайдер недоступен из этой сети: до следующего обновления настройки — путь через функцию."""
        with self._lock:
            self._down_until = self.clock() + self.ttl
            self._cached = None

    def report(self, model: str, usage: dict | None, lab: str | None, chat: str | None) -> None:
        """Расход прямого запроса — в функцию, в фоне: ответ модели не ждёт учёта, сбой учёта не виден."""
        u = usage or {}
        body = {"model": model, "prompt_tokens": int(u.get("prompt_tokens") or 0),
                "completion_tokens": int(u.get("completion_tokens") or 0), "lab": lab, "chat": chat}

        def send():
            try:
                self.http.post(f"{self.function_url}/usage", json=body,
                               headers={"Authorization": f"Bearer {self.token()}"}, timeout=30)
            except Exception as e:  # noqa: BLE001 — учёт не должен мешать работе
                log.debug("Расход прямого запроса не отправлен: %s", type(e).__name__)

        t = threading.Thread(target=send, name="forlabs-usage", daemon=True)
        self._reports = [x for x in self._reports if x.is_alive()] + [t]
        t.start()

    def wait(self, timeout: float = 10.0) -> None:
        """Дождаться отправки расхода (для тестов и выхода из программы)."""
        for t in list(self._reports):
            t.join(timeout)
