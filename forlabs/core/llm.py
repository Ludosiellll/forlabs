"""Клиент LLM (OpenAI-совместимый API): напрямую с ключом или через прокси Supabase с токеном пользователя."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from forlabs.core.netcert import CERT_MESSAGE, is_cert_error
from forlabs.core.settings import MODEL_FALLBACKS, Settings

T = TypeVar("T", bound=BaseModel)
Kind = Literal["auth", "model", "quota", "network", "bad_response", "access"]

_MESSAGES = {
    "auth": "Ключ API не принят. Проверь ключ в настройках или войди в аккаунт заново.",
    "model": "Эта модель не входит в ключ. Выбери в настройках другую модель DeepSeek (по умолчанию deepseek-v4.1-flash-1017).",
    "quota": "Закончился лимит или баланс ключа, либо слишком много запросов подряд. Подожди или пополни ключ.",
    "network": "Нет связи с сервером модели. Проверь интернет и адрес API в настройках.",
    "bad_response": "Модель несколько раз ответила не в том формате. Попробуй ещё раз.",
    "access": "Нет доступа к модели через облако.",
}
_ACCESS = {
    "not approved": "Аккаунт ещё не одобрен администратором. Попроси его открыть раздел «Админ» и одобрить тебя.",
    "member key rejected": "Твой ключ модели не принят провайдером — напиши администратору, чтобы он его заменил.",
    "member key balance": "На твоём ключе закончился баланс — напиши администратору, чтобы он пополнил или заменил ключ.",
    "llm_api_key": "Администратор ещё не добавил ключ модели в Supabase (секрет LLM_API_KEY).",
    "not signed in": "Сессия истекла — войди в аккаунт заново.",
    "no key assigned": "Администратор ещё не выдал тебе ключ модели. Напиши ему — он добавит ключ в разделе «Админ».",
    "trial lab calls limit": "В пробном доступе на эту лабу ушло слишком много запросов (много перезапусков). Напиши "
                             "в поддержку, чтобы открыть полный доступ.",
    "trial needs lab tag": "Обнови ForLabs до последней версии — эта версия не работает с пробным доступом.",
}


DEFAULT_MAX_TOKENS = 8000  # предел ответа: ни один вызов не растянется дольше лимита прокси (~150 с)
# С размышлением: оно засчитывается в предел ответа. 16 000 токенов модель пишет примерно за 70 с
# (≈230 токенов/с), это в пределах 140 с, которые прокси ждёт провайдера.
THINK_MAX_TOKENS = 16000
TIMEOUT_MESSAGE = ("Модель не ответила за 2,5 минуты — сервер модели перегружен. Подожди пару минут и нажми ещё раз "
                   "(написанное сохранено).")
RATE_LIMIT_MESSAGE = "Сервер модели просит подождать: слишком много запросов подряд. Подожди минуту и нажми ещё раз."
MAX_RETRY_AFTER = 60.0
# прямой путь со своим ключом участника (forlabs.core.direct)
KEY_BROKEN_MESSAGE = "Ключ не работает — напиши администратору."
KEY_EMPTY_MESSAGE = "На ключе закончились токены — напиши администратору."
DIRECT_TIMEOUT_MESSAGE = ("Модель не ответила за 10 минут. Подожди пару минут и нажми ещё раз (написанное "
                          "сохранено).")
GLM_HEADROOM = 4000  # GLM рассуждает всегда, и рассуждение входит в предел ответа: запас сверх предела шага


def _retry_after(value: str | None) -> float | None:
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:  # дата вместо секунд — обычная пауза
        return None


def _minsk_date(iso: str) -> str:
    """Дата окончания подписки так же, как в шапке приложения: по Минску (UTC+3)."""
    from datetime import datetime, timedelta, timezone

    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone(timedelta(hours=3))).strftime("%d.%m.%Y")


class LLMError(Exception):
    def __init__(self, kind: Kind, detail: str = "", message_ru: str | None = None, retryable: bool = False,
                 retry_after: float | None = None):
        self.kind = kind
        self.detail = detail
        self.retryable = retryable  # временный сбой: сеть, перегрузка сервера, частые запросы
        self.retry_after = retry_after  # сколько секунд просит подождать сервер (заголовок Retry-After)
        self.unauthorized = False  # прокси не принял вход: можно обновить вход и повторить один раз
        self.message_ru = message_ru or _MESSAGES[kind]
        super().__init__(f"{self.message_ru} ({detail})" if detail else self.message_ru)


@dataclass
class UsageCounter:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    requests: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, usage: dict | None) -> None:
        with self._lock:
            self.requests += 1
            if usage:
                self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
                self.completion_tokens += int(usage.get("completion_tokens") or 0)


def _extract_json(text: str) -> Any:
    text = text.strip()
    try:  # сначала как есть: ``` внутри строки (листинг) — не обёртка
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.match(r"```(?:json)?\s*(.+?)```\s*$", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
        if start >= 0:
            end = max(text.rfind("}"), text.rfind("]"))
            if end > start:
                return json.loads(text[start:end + 1])
        raise


def error_for(status: int, detail: str, retry_after: str | None = None) -> LLMError:
    """Ошибочный ответ функции llm или провайдера → понятная студенту ошибка (одна для обоих путей)."""
    low = detail.lower()
    expired = re.search(r"subscription expired:\s*([0-9T:.\-+Z]+)", detail)
    if expired:
        return LLMError("access", detail, f"Подписка закончилась {_minsk_date(expired.group(1))} — напиши "
                                         "администратору, чтобы он её продлил.")
    if "too many parallel requests" in low:  # уже идут 4 запроса — подождать и повторить
        return LLMError("quota", detail, RATE_LIMIT_MESSAGE, retryable=True, retry_after=20)
    if "request too large" in low:
        return LLMError("bad_response", detail, "Методичка слишком большая для модели — оставь в файле только "
                                               "страницы своей лабы.")
    daily = re.search(r"daily token limit:\s*(\d+)", detail)
    if daily:
        n = f"{int(daily.group(1)):,}".replace(",", " ")
        return LLMError("access", detail, f"Дневной лимит на сегодня исчерпан ({n} токенов). Продолжить можно "
                                         "завтра; если нужно срочно — напиши в поддержку.")
    calls = re.search(r"lab calls limit:\s*(\d+)", detail)
    if calls:
        return LLMError("access", detail, f"На эту лабу ушло очень много запросов ({calls.group(1)}) — похоже на "
                                         "лишние перезапуски. Если нужно ещё, напиши в поддержку.")
    trial = re.search(r"trial (labs|chat) limit:\s*(\d+)", detail)
    if trial:
        n = trial.group(2)
        return LLMError("access", detail, (
            f"Пробный доступ: все лабы ({n}) уже начаты. Чтобы сделать ещё, напиши в поддержку — "
            "администратор откроет полный доступ." if trial.group(1) == "labs" else
            f"Пробный доступ: на одну лабу — {n} сообщений в чате правок, они закончились. Напиши в поддержку, "
            "чтобы открыть полный доступ."))
    for needle, msg in _ACCESS.items():
        if needle in low:
            err = LLMError("access", detail, msg)
            err.unauthorized = needle == "not signed in"
            return err
    if status in (400, 413) and any(w in low for w in ("context length", "maximum context",
                                                             "too many tokens", "too long", "token limit")):
        return LLMError("bad_response", detail, "Методичка слишком большая для модели — оставь в файле только "
                                               "страницы своей лабы.")
    if status == 401:
        return LLMError("auth", detail)
    if status == 403:
        return LLMError("model" if "model" in low else "auth", detail)
    if status in (402, 429) or "balance" in low or "quota" in low:
        if status == 429 and not ("balance" in low or "quota" in low):  # просто часто — не баланс
            return LLMError("quota", detail, RATE_LIMIT_MESSAGE, retryable=True,
                           retry_after=_retry_after(retry_after))
        return LLMError("quota", detail)
    if status in (504, 546) or "provider timeout" in low or "worker_limit" in low:
        # прокси оборвал долгий запрос по времени: повтор того же запроса оборвётся так же и снова оплатится
        return LLMError("network", f"сервер ответил {status}: {detail}", TIMEOUT_MESSAGE)
    if status >= 500:
        return LLMError("network", f"сервер ответил {status}: {detail}", retryable=True)
    return LLMError("bad_response", f"{status}: {detail}")


def _glm(model: str) -> bool:
    return (model or "").startswith("glm")


class LLM:
    accepts_lab_tag = False  # True только для прокси ForLabs в облаке
    direct = None  # DirectAccess: свой ключ участника — к провайдеру напрямую (облако)
    on_unauthorized: "Callable[[], object] | None" = None  # облако: обновить вход при 401 (сбитые часы)
    def __init__(self, settings: Settings, api_key: "str | Callable[[], str]", http_client: httpx.Client | None = None,
                 base_url: str | None = None, timeout: float = 240.0,
                 retry_delays: tuple[float, ...] = (3.0, 10.0)):
        self.settings = settings
        self.retry_delays = retry_delays
        self.extra_headers: dict[str, str] = {}
        self.model = settings.model
        self.base_url = (base_url or settings.base_url).rstrip("/")
        self.api_key = api_key
        self.http = http_client or httpx.Client(timeout=timeout)
        self.usage = UsageCounter()
        self.think_fallbacks = 0  # сколько раз размышление не уложилось и ответ получен без него
        # предел ответа на прокси (заголовок x-forlabs-max-tokens); общий для копий клиента (with_headers)
        self.server: dict[str, int | None] = {"max_tokens": None}

    def _post(self, payload: dict) -> dict:
        """Запрос; если ключ не пускает модель — следующая модель из MODEL_FALLBACKS (и запоминается)."""
        tried: set[str] = set()
        while True:
            try:
                return self._post_retrying(payload)
            except LLMError as e:
                current = payload.get("model", self.model)
                tried.add(current)
                nxt = next((m for m in MODEL_FALLBACKS.get(current, []) if m not in tried), None)
                if e.kind != "model" or nxt is None:
                    raise
                payload["model"] = self.model = nxt

    def _post_retrying(self, payload: dict) -> dict:
        """Запрос с повторами при временных сбоях; ключ, лимит и баланс не повторяются."""
        attempt, refreshed, refetched, fell_back = 0, False, False, False
        while True:
            try:
                return self._post_once(payload)
            except LLMError as e:
                if e.unauthorized and self.on_unauthorized is not None and not refreshed:
                    refreshed = True
                    try:
                        self.on_unauthorized()
                    except Exception:
                        raise e from None
                    continue
                if getattr(e, "direct_key", False) and self.direct is not None and not refetched:
                    refetched = True  # администратор мог заменить ключ: настройку — заново, запрос — ещё раз
                    self.direct.invalidate()
                    continue
                if getattr(e, "direct_net", False) and self.direct is not None and not fell_back and (
                        not e.retryable or attempt >= len(self.retry_delays)):
                    fell_back, attempt = True, 0  # провайдер недоступен из этой сети — через функцию llm
                    self.direct.mark_down()
                    continue
                if not e.retryable or attempt >= len(self.retry_delays):
                    raise
                time.sleep(max(self.retry_delays[attempt], min(e.retry_after or 0, MAX_RETRY_AFTER)))
                attempt += 1

    def _direct_cfg(self):
        """Настройка прямого доступа или None; сбой связи с функцией — None (запрос пойдёт её путём с повторами)."""
        if self.direct is None:
            return None
        try:
            return self.direct.config()
        except LLMError as e:
            if e.retryable:
                return None
            raise

    def _post_direct(self, payload: dict, cfg) -> dict:
        """Запрос прямо к провайдеру со своим ключом участника; метки лабы и чата — только в учёт расхода."""
        from urllib.parse import unquote

        body = {**payload, "model": cfg.model}
        try:
            r = self.http.post(f"{cfg.base_url}/chat/completions", json=body, timeout=cfg.timeout_s,
                               headers={"Authorization": f"Bearer {cfg.key}", "Content-Type": "application/json"})
        except httpx.ReadTimeout as e:  # модель думала дольше предела: повтор оборвётся так же и снова оплатится
            raise LLMError("network", str(e), DIRECT_TIMEOUT_MESSAGE) from e
        except (httpx.TransportError, httpx.TimeoutException) as e:
            err = LLMError("network", str(e), CERT_MESSAGE if is_cert_error(e) else None,
                           retryable=not is_cert_error(e))
            err.direct_net = True
            raise err from e
        if r.status_code >= 400:
            detail = r.text[:300]
            low = detail.lower()
            money = any(w in low for w in ("balance", "quota", "insufficient", "credit"))
            if r.status_code == 402 or (r.status_code in (403, 429) and money):
                raise LLMError("quota", detail, KEY_EMPTY_MESSAGE)
            if r.status_code in (401, 403) and "model" not in low:
                err = LLMError("auth", detail, KEY_BROKEN_MESSAGE)
                err.direct_key = True
                raise err
            err = error_for(r.status_code, detail, r.headers.get("retry-after"))
            err.direct_net = r.status_code >= 500
            raise err
        try:
            data = r.json()
        except ValueError as e:
            raise LLMError("bad_response", r.text[:200]) from e
        self.usage.add(data.get("usage"))
        self.server["max_tokens"] = cfg.max_tokens
        lab, chat = self.extra_headers.get("X-ForLabs-Lab"), self.extra_headers.get("X-ForLabs-Chat")
        self.direct.report(cfg.model, data.get("usage"), unquote(lab) if lab else None, unquote(chat) if chat else None)
        return data

    def _post_once(self, payload: dict) -> dict:
        cfg = self._direct_cfg()
        if cfg is not None:
            return self._post_direct(payload, cfg)
        try:
            key = self.api_key() if callable(self.api_key) else self.api_key
        except Exception as e:  # обновление токена облака не удалось
            if getattr(e, "offline", False):  # обрыв связи в момент обновления входа — повторяемо
                raise LLMError("network", str(e), getattr(e, "message_ru", None), retryable=True) from e
            raise LLMError("access", str(e), getattr(e, "message_ru", None) or
                           "Не удалось получить доступ к модели. Войди в аккаунт заново.") from e
        try:
            r = self.http.post(f"{self.base_url}/chat/completions", json=payload,
                               headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                        **self.extra_headers})
        except (httpx.TransportError, httpx.TimeoutException) as e:
            if is_cert_error(e):  # сам не пройдёт — повторять бессмысленно
                raise LLMError("network", str(e), CERT_MESSAGE) from e
            raise LLMError("network", str(e), retryable=True) from e
        if r.status_code >= 400:
            raise error_for(r.status_code, r.text[:300], r.headers.get("retry-after"))
        try:
            data = r.json()
        except ValueError as e:
            raise LLMError("bad_response", r.text[:200]) from e
        self.usage.add(data.get("usage"))
        cap = r.headers.get("x-forlabs-max-tokens", "")
        if cap.isdigit():
            self.server["max_tokens"] = int(cap)
        return data

    def with_headers(self, extra: dict[str, str]) -> "LLM":
        """Копия клиента с дополнительными заголовками (метка лабы); http-клиент и счётчик расхода общие."""
        import copy

        clone = copy.copy(self)
        clone.extra_headers = {**self.extra_headers, **extra}
        return clone

    def chat(self, messages: list[dict], temperature: float | None = None, max_tokens: int | None = None,
             json_mode: bool = False, think: bool = False, _full: bool = False) -> str:
        """think — модель сначала рассуждает (уровень из настроек). Рассуждение непредсказуемо по длине: если оно
        съело весь предел ответа или запрос не уложился по времени, тот же запрос повторяется без размышления."""
        payload: dict[str, Any] = {"model": self.model, "messages": messages,
                                   "temperature": self.settings.temperature if temperature is None else temperature}
        cfg = self._direct_cfg()
        glm = _glm(cfg.model if cfg is not None else self.model)
        cap = cfg.max_tokens if cfg is not None else THINK_MAX_TOKENS
        effort = (getattr(self.settings, "reasoning", "off") or "off") if think else "off"
        if (effort != "off" and cfg is None and self.settings.mode == "cloud"
                and (self.server["max_tokens"] or 0) < THINK_MAX_TOKENS):
            effort = "off"  # прокси ещё старый (предел 8000): размышление съело бы весь ответ
        if cfg is not None and effort == "low":
            effort = "medium"  # прямой путь без предела времени функции: на сложных шагах — подумать подольше
        if effort != "off":
            payload["reasoning_effort"] = effort
            payload["max_tokens"] = max(max_tokens or 0, THINK_MAX_TOKENS)
        elif max_tokens:
            payload["max_tokens"] = max_tokens
        if glm:  # рассуждает всегда: без уровня думает до предела ответа и не отвечает
            payload.setdefault("reasoning_effort", "low")
            payload["max_tokens"] = min((payload.get("max_tokens") or DEFAULT_MAX_TOKENS) + GLM_HEADROOM, cap)
            if _full:  # ответ уже обрезался: весь предел пути и короткое рассуждение
                payload["reasoning_effort"], payload["max_tokens"] = "low", cap
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            data = self._post(payload)
        except LLMError as e:
            if effort != "off" and e.message_ru == TIMEOUT_MESSAGE:
                self.think_fallbacks += 1
                return self.chat(messages, temperature, max_tokens, json_mode, think=False)
            raise
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("bad_response", str(data)[:200]) from e
        if choice.get("finish_reason") == "length":  # обрезан по длине: «чинить» формат бессмысленно и платно
            if glm and not _full:  # рассуждение заняло предел шага — один повтор со всем пределом пути
                self.think_fallbacks += 1
                return self.chat(messages, temperature, max_tokens, json_mode, think=False, _full=True)
            if effort != "off" and not glm:  # размышление заняло весь предел — ответ без размышления, как раньше
                self.think_fallbacks += 1
                return self.chat(messages, temperature, max_tokens, json_mode, think=False)
            raise LLMError("bad_response", "finish_reason=length",
                           "Ответ модели не поместился в лимит длины — попробуй ещё раз или раздели задание на части.")
        return content

    def json(self, messages: list[dict], schema: type[T], repairs: int = 3, temperature: float | None = None,
             max_tokens: int = DEFAULT_MAX_TOKENS, think: bool = False) -> T:
        msgs = list(messages)
        last_error = ""
        for attempt in range(repairs + 1):
            # исправление формата — без размышления: ошибка обычно мелкая, а размышление дорогое
            text = self.chat(msgs, temperature=temperature, json_mode=True, max_tokens=max_tokens,
                             think=think and attempt == 0)
            try:
                return schema.model_validate(_extract_json(text))
            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                last_error = str(e)[:800]
                msgs = msgs + [{"role": "assistant", "content": text},
                               {"role": "user", "content": "В ответе ошибка формата, исправь и верни только JSON "
                                                           f"по схеме. Ошибка: {last_error}"}]
        raise LLMError("bad_response", last_error)

    def check(self) -> str:
        payload = {"model": self.model, "messages": [{"role": "user", "content": "Ответь одним словом: да"}],
                   "max_tokens": 5}
        data = self._post(payload)
        return data.get("model") or self.model


class FakeLLM:
    """Записанные ответы для тестов: dict → для json(), str → для chat(); Exception → бросается."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []
        self.usage = UsageCounter()
        self.model = "fake"
        self.headers: dict[str, str] = {}
        self.accepts_lab_tag = True
        self.max_tokens: list[int | None] = []
        self.thinks: list[bool] = []  # просил ли вызов размышление
        self.think_fallbacks = 0

    def with_headers(self, extra: dict[str, str]) -> "FakeLLM":
        self.headers = {**self.headers, **extra}
        return self

    def _next(self, messages):
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("FakeLLM: ответы закончились")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def chat(self, messages, temperature=None, max_tokens=None, json_mode=False, think=False) -> str:
        self.thinks.append(think)
        r = self._next(messages)
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)

    def json(self, messages, schema, repairs=3, temperature=None, max_tokens=DEFAULT_MAX_TOKENS, think=False):
        self.max_tokens.append(max_tokens)
        self.thinks.append(think)
        r = self._next(messages)
        return schema.model_validate(r if not isinstance(r, str) else _extract_json(r))

    def check(self) -> str:
        return "fake"
