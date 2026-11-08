"""Поддержка: переписка студента с администратором (таблица support_messages, функция support → Telegram)."""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from forlabs.cloud.auth import CloudError
from forlabs.core.netcert import offline_message

RATE_LIMIT_MESSAGE = "Слишком много сообщений за час — подожди немного, администратор уже получил твои сообщения."
SECRETS_MISSING = ("В Supabase не заданы секреты бота: TELEGRAM_BOT_TOKEN (токен от @BotFather) и TELEGRAM_WEBHOOK_SECRET "
                   "(любая длинная случайная строка). Добавь их в Supabase → Edge Functions → Secrets и нажми ещё раз.")


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
            return self.s.http.post(f"{self.s.cfg.url}/functions/v1/support/{name}", json=body, headers=self.s._h(),
                                    timeout=30)
        except httpx.TransportError as e:
            raise CloudError(offline_message(e), str(e), offline=True) from e

    # студент
    def messages(self, uid: str | None = None) -> list[SupportMessage]:
        rows = self.s._rest("GET", "support_messages", params={
            "select": "*", "user_id": f"eq.{uid or self.s.auth.user_id}", "order": "created_at.asc", "limit": "500"}).json()
        return [SupportMessage.model_validate(r) for r in rows]

    def send(self, text: str) -> bool:
        """Отправить администратору; True — ушло и в Telegram."""
        r = self._fn("send", {"text": text})
        if r.status_code == 429:
            raise CloudError(RATE_LIMIT_MESSAGE, r.text[:200])
        self.s._check(r)
        return bool(r.json().get("telegram"))

    def unread(self) -> int:
        rows = self.s._read("support_messages", {"select": "id", "user_id": f"eq.{self.s.auth.user_id}",
                                                 "from_admin": "eq.true", "read_at": "is.null", "limit": "20"})
        return len(rows or [])

    def mark_read(self) -> None:
        self.s._rpc("support_mark_read")

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
                     prefer="return=minimal")

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
            raise CloudError("Telegram не принял токен бота — проверь секрет TELEGRAM_BOT_TOKEN в Supabase.", r.text[:200])
        self.s._check(r)
        return r.json()
