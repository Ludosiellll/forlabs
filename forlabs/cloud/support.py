"""Поддержка: переписка студента с администратором (таблица support_messages, функция support → Telegram)."""

from __future__ import annotations

import io
import re
import uuid
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from forlabs.cloud.auth import CloudError
from forlabs.core.netcert import offline_message

RATE_LIMIT_MESSAGE = "Слишком много сообщений за час — подожди немного, администратор уже получил твои сообщения."
SECRETS_MISSING = ("В Supabase не заданы секреты бота: TELEGRAM_BOT_TOKEN (токен от @BotFather) и TELEGRAM_WEBHOOK_SECRET "
                   "(40–60 символов: латиница, цифры, _ и -). Добавь их в Supabase → Edge Functions → Secrets и нажми ещё раз.")


NOT_READY = ("Поддержка ещё не включена на сервере — администратору нужно выполнить supabase/migrations/0006_support.sql "
             "и выложить функцию support (README, «Обновление сервера»).")
UNREAD_TTL = 45.0  # секунд: значок нового ответа не делает запрос на каждой странице


def _not_ready(e: CloudError) -> bool:
    d = (e.detail or "").lower()
    return not e.offline and ("support_messages" in d or "42p01" in d or "404" in e.message_ru or "not found" in d)


MAX_IMAGES = 4
MAX_PIXELS = 40_000_000
MAX_IMAGE_BYTES = 15_000_000
BUCKET = "support"
_PATH = re.compile(r"^[0-9a-fA-F-]{36}/[A-Za-z0-9._-]{1,80}$")


def to_jpeg(data: bytes) -> bytes:
    """Снимок экрана или фото → JPEG не больше 2560 px (Telegram принимает как фото, места меньше)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > MAX_PIXELS:  # до распаковки: огромная картинка съела бы память
            raise ValueError("Картинка слишком большая — сделай снимок поменьше (до 40 мегапикселей)")
        img.draft("RGB", (2560, 2560))  # JPEG: сразу уменьшенный
        img.load()
    except ValueError:
        raise
    except Image.DecompressionBombError as e:
        raise ValueError("Картинка слишком большая — сделай снимок поменьше (до 40 мегапикселей)") from e
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError("Это не картинка — прикрепи снимок экрана или фото (PNG, JPEG)") from e
    img = ImageOps.exif_transpose(img)
    if img.mode in ("I;16", "I;16B", "I;16L", "I"):  # 16-битный серый: иначе получится белый лист
        img = img.point(lambda v: v / 256).convert("L")
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((2560, 2560))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue()


def valid_path(path: str) -> bool:
    return bool(_PATH.match(path or "")) and ".." not in path


class SupportMessage(BaseModel):
    id: int
    user_id: str
    from_admin: bool = False
    text: str = ""
    images: list[str] = []
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
            rows = self.s._rest("GET", "support_messages", params={  # последние 500, по порядку
                "select": "*", "user_id": f"eq.{uid or self.s.auth.user_id}", "order": "created_at.desc",
                "limit": "500"}).json()[::-1]
        except CloudError as e:
            if _not_ready(e):
                raise CloudError(NOT_READY, e.detail) from e
            raise
        return [SupportMessage.model_validate(r) for r in rows]

    def _upload(self, owner_uid: str, images: list[bytes], prefix: str) -> list[str]:
        if len(images) > MAX_IMAGES:
            raise ValueError(f"Можно прикрепить не больше {MAX_IMAGES} фото")
        paths = []
        for data in images:
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError("Фото слишком большое — больше 15 МБ")
            jpeg = to_jpeg(data)
            path = f"{owner_uid}/{prefix}{uuid.uuid4().hex[:12]}.jpg"
            r = self.s._storage("POST", f"object/{BUCKET}/{quote(path)}", content=jpeg,
                                headers={"Content-Type": "image/jpeg", "x-upsert": "false"})
            if r.status_code == 404 or (r.status_code == 400 and "bucket" in r.text.lower()):
                raise CloudError(NOT_READY, r.text[:200])
            self.s._check(r)
            paths.append(path)
        return paths

    def image(self, path: str) -> bytes | None:
        if not valid_path(path):
            return None
        r = self.s._storage("GET", f"object/{BUCKET}/{quote(path)}")
        if r.status_code in (400, 404):
            return None
        self.s._check(r)
        return r.content

    def send(self, text: str, images: list[bytes] | None = None) -> bool:
        """Отправить администратору (с фото — сначала в хранилище); True — ушло и в Telegram."""
        paths = self._upload(self.s.auth.user_id, images or [], "") if images else []
        r = self._fn("send", {"text": text, "images": paths})
        if r.status_code == 429:
            raise CloudError(RATE_LIMIT_MESSAGE, r.text[:200])
        self.s._check(r)
        return bool(r.json().get("telegram"))

    def unread(self) -> int:
        import time

        cached = getattr(self.s, "_support_unread", None)
        uid = self.s.auth.user_id

        def fetch() -> int:
            try:
                rows = self.s._read("support_messages", {"select": "id", "user_id": f"eq.{uid}",
                                                         "from_admin": "eq.true", "read_at": "is.null", "limit": "20"})
            except CloudError:
                rows = []
            n = len(rows or [])
            self.s._support_unread = (time.monotonic(), n)
            return n

        if cached and UNREAD_TTL > 0:
            if time.monotonic() - cached[0] >= UNREAD_TTL and hasattr(self.s, "_refresh"):
                # страница не ждёт: значок покажет прежнее число, свежее придёт к следующему переходу
                self.s._refresh(("unread", uid), "unread", 0, lambda: fetch() and None)
                return cached[1]
            if time.monotonic() - cached[0] < UNREAD_TTL:
                return cached[1]
        return fetch()

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

    def reply(self, uid: str, text: str, images: list[bytes] | None = None) -> None:
        paths = self._upload(uid, images or [], "admin-") if images else []
        row = {"user_id": uid, "from_admin": True, "text": text, **({"images": paths} if paths else {})}
        self.s._rest("POST", "support_messages", json=row,
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

    def check(self) -> dict:
        """Проверка бота: {"bound", "bot", "items": [{"ok", "text"}]} — по пунктам, что работает и что сделать."""
        r = self._fn("check", {})
        self.s._check(r)
        d = r.json()
        items: list[dict] = []

        def item(ok: bool, text: str) -> None:
            items.append({"ok": ok, "text": text})

        bot = d.get("bot")
        if not d.get("secrets"):
            item(False, "В Supabase не заданы секреты TELEGRAM_BOT_TOKEN и TELEGRAM_WEBHOOK_SECRET "
                        "(Edge Functions → Secrets).")
        elif d.get("token_ok") is False:
            item(False, "Telegram не принял токен бота. Возьми токен у @BotFather (команда /token) и вставь его "
                        "в TELEGRAM_BOT_TOKEN: Supabase → Edge Functions → Secrets.")
        else:
            item(True, f"Токен принят: бот @{bot}." if bot else "Токен принят.")
            if not d.get("webhook_ok"):
                item(False, "Бот ещё не настроен на ForLabs — нажми «Подключить Telegram».")
            elif d.get("webhook_error"):
                item(False, f"Telegram не может достучаться до ForLabs: {d['webhook_error']}. Нажми «Подключить "
                            "Telegram» ещё раз.")
            if d.get("bound"):
                item(True, f"Подключено: сообщения студентов приходят в твой чат с @{bot}." if bot else
                     "Подключено: сообщения студентов приходят тебе в Telegram.")
            elif d.get("code_active"):
                item(False, "Жду: открой ссылку ниже в Telegram и нажми «Запустить».")
            elif d.get("webhook_ok"):
                item(False, "Чат ещё не подключён — нажми «Подключить Telegram» и открой ссылку.")
        return {"bound": bool(d.get("bound")), "bot": bot, "items": items}

    def setup(self) -> dict:
        r = self._fn("setup", {})
        if r.status_code == 503:
            raise CloudError(SECRETS_MISSING, r.text[:200])
        if r.status_code == 502:
            raise CloudError("Telegram не принял настройку бота — проверь секреты в Supabase: TELEGRAM_BOT_TOKEN (токен от "
                             "@BotFather) и TELEGRAM_WEBHOOK_SECRET (только латиница, цифры, _ и -).", r.text[:200])
        if r.status_code == 500 and "link not saved" in r.text:
            raise CloudError("Сервер не сохранил ссылку для Telegram, поэтому бот её не узнает. В Supabase открой "
                             "SQL Editor и выполни файл supabase/migrations/0012_support_link_saved.sql, потом нажми "
                             "«Подключить Telegram» ещё раз.", r.text[:300])
        self.s._check(r)
        return r.json()
