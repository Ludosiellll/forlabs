"""Поддержка: переписка студента с администратором (таблица support_messages, функция support → Telegram)."""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from forlabs.cloud.auth import CloudError
from forlabs.core.netcert import offline_message

RATE_LIMIT_MESSAGE = "Слишком много сообщений за час — подожди немного, администратор уже получил твои сообщения."
SECRETS_MISSING = ("В Supabase не заданы секреты бота: TELEGRAM_BOT_TOKEN (токен от @BotFather) и TELEGRAM_WEBHOOK_SECRET "
                   "(40–60 символов: латиница, цифры, _ и -). Добавь их в Supabase → Edge Functions → Secrets и нажми ещё раз.")


NOT_READY = ("Поддержка ещё не включена на сервере — администратору нужно выполнить supabase/migrations/0006_support.sql "
             "и выложить функцию support (README, «Обновление сервера»).")
UNREAD_TTL = 20.0  # секунд: значок нового ответа не делает запрос на каждой странице


def _not_ready(e: CloudError) -> bool:
    d = (e.detail or "").lower()
    return not e.offline and ("support_messages" in d or "42p01" in d or "404" in e.message_ru or "not found" in d)


class SupportMessage(BaseModel):
    id: int
    user_id: str
    from_admin: bool = False
    text: str
    created_at: str = ""
    read_at: str | None = None


class Thread(BaseModel):
    user_id: str
    last: SupportMessage
    unread: int = 0  # сообщений студента, которые администратор ещё не прочитал


class Support:
    def __init__(self, store):
        self.s = store

    def _fn(self, name: str, body: dict) -> httpx.Response:
        try:
            r = self.s.http.post(f"{self.s.cfg.url}/functions/v1/support/{name}", json=body, headers=self.s._h(),
                                 timeout=30)
        except httpx.TransportError as e:
            raise CloudError(offline_message(e), str(e), offline=True) from e
        if r.status_code == 404:
            raise CloudError(NOT_READY, r.text[:200])
        return r

    # студент
    def messages(self, uid: str | None = None) -> list[SupportMessage]:
        try:
            rows = self.s._rest("GET", "support_messages", params={
                "select": "*", "user_id": f"eq.{uid or self.s.auth.user_id}", "order": "created_at.asc",
                "limit": "500"}).json()
        except CloudError as e:
            if _not_ready(e):
                raise CloudError(NOT_READY, e.detail) from e
            raise
        return [SupportMessage.model_validate(r) for r in rows]

    def send(self, text: str) -> bool:
        """Отправить администратору; True — ушло и в Telegram."""
        r = self._fn("send", {"text": text})
        if r.status_code == 429:
            raise CloudError(RATE_LIMIT_MESSAGE, r.text[:200])
        self.s._check(r)
        return bool(r.json().get("telegram"))

    def unread(self) -> int:
        import time

        cached = getattr(self.s, "_support_unread", None)
        if cached and time.monotonic() - cached[0] < UNREAD_TTL:
            return cached[1]
        try:
            rows = self.s._read("support_messages", {"select": "id", "user_id": f"eq.{self.s.auth.user_id}",
                                                     "from_admin": "eq.true", "read_at": "is.null", "limit": "20"})
        except CloudError:
            rows = []
        n = len(rows or [])
        self.s._support_unread = (time.monotonic(), n)
        return n

    def mark_read(self) -> None:
        self.s._rpc("support_mark_read")
        self.s._support_unread = None

    # администратор
    def threads(self) -> list[Thread]:
        rows = self.s._rest("GET", "support_messages", params={"select": "*", "order": "created_at.desc",
                                                               "limit": "1000"}).json()
        out: dict[str, Thread] = {}
        for r in rows:
            m = SupportMessage.model_validate(r)
            t = out.setdefault(m.user_id, Thread(user_id=m.user_id, last=m))
            if not m.from_admin and not m.read_at:
                t.unread += 1
        return sorted(out.values(), key=lambda t: t.last.created_at, reverse=True)

    def reply(self, uid: str, text: str) -> None:
        self.s._rest("POST", "support_messages", json={"user_id": uid, "from_admin": True, "text": text},
                     prefer="return=minimal", retries=False)  # повтор вставки после обрыва задвоил бы ответ

    def admin_mark_read(self, uid: str) -> None:
        from forlabs.cloud.admin import now_utc

        self.s._rest("PATCH", "support_messages", params={"user_id": f"eq.{uid}", "from_admin": "eq.false",
                                                          "read_at": "is.null"},
                     json={"read_at": now_utc().isoformat()}, prefer="return=minimal")

    def status(self) -> tuple[bool, str | None]:
        rows = self.s._rpc("admin_support_status") or []
        row = rows[0] if rows else {}
        return bool(row.get("telegram")), row.get("bot_username")

    def setup(self) -> dict:
        r = self._fn("setup", {})
        if r.status_code == 503:
            raise CloudError(SECRETS_MISSING, r.text[:200])
        if r.status_code == 502:
            raise CloudError("Telegram не принял настройку бота — проверь секреты в Supabase: TELEGRAM_BOT_TOKEN (токен от "
                             "@BotFather) и TELEGRAM_WEBHOOK_SECRET (только латиница, цифры, _ и -).", r.text[:200])
        self.s._check(r)
        return r.json()
