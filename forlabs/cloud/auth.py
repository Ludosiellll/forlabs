"""Вход в облако (Supabase Auth): e-mail и пароль, автообновление токена, хранение сессии."""

from __future__ import annotations

import json
import os
import threading
import time

import httpx
from pydantic import BaseModel

from forlabs.core.fs import atomic_write_text, quarantine

from forlabs.cloud.config import DEFAULT, CloudConfig
from forlabs.core import paths

KEYRING_SERVICE = "ForLabs"
KEYRING_SESSION = "cloud_session"


class CloudError(Exception):
    def __init__(self, message_ru: str, detail: str = ""):
        self.message_ru = message_ru
        self.detail = detail
        super().__init__(message_ru)


class Session(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: int
    user_id: str
    email: str = ""


def _error_from(r: httpx.Response) -> CloudError:
    try:
        data = r.json()
    except ValueError:
        data = {}
    text = " ".join(str(data.get(k, "")) for k in ("error", "error_description", "msg", "message", "error_code")).lower()
    if "invalid login" in text or "invalid_grant" in text and "refresh" not in text:
        return CloudError("Неверный e-mail или пароль.", text)
    if "not confirmed" in text:
        return CloudError("Подтверди почту: открой ссылку из письма Supabase и войди снова.", text)
    if "already registered" in text or "already exists" in text:
        return CloudError("Такой e-mail уже зарегистрирован — просто войди.", text)
    if "password" in text and ("weak" in text or "least" in text or "short" in text):
        return CloudError("Пароль слишком простой: нужно не меньше 6 символов.", text)
    if "rate limit" in text or r.status_code == 429:
        return CloudError("Слишком много попыток. Подожди пару минут.", text)
    if "refresh" in text:
        return CloudError("Сессия истекла — войди снова.", text)
    return CloudError(f"Облако ответило ошибкой {r.status_code}.", text or r.text[:200])


class CloudAuth:
    def __init__(self, config: CloudConfig = DEFAULT, http: httpx.Client | None = None, use_keyring: bool = True):
        self.config = config
        self.http = http or httpx.Client(timeout=30)
        self.use_keyring = use_keyring
        self.session: Session | None = None
        self._lock = threading.Lock()

    # хранение
    def _session_file(self):
        return paths.data_home() / "cloud_session.json"

    def _save(self) -> None:
        raw = self.session.model_dump_json() if self.session else ""
        if self.use_keyring:
            try:
                import keyring

                if raw:
                    keyring.set_password(KEYRING_SERVICE, KEYRING_SESSION, raw)
                else:
                    keyring.delete_password(KEYRING_SERVICE, KEYRING_SESSION)
                return
            except Exception:
                pass
        f = self._session_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        if raw:
            atomic_write_text(f, raw)
            try:
                os.chmod(f, 0o600)
            except OSError:
                pass
        elif f.exists():
            f.unlink()

    def load_session(self) -> Session | None:
        raw = None
        if self.use_keyring:
            try:
                import keyring

                raw = keyring.get_password(KEYRING_SERVICE, KEYRING_SESSION)
            except Exception:
                raw = None
        if not raw and self._session_file().exists():
            try:
                raw = self._session_file().read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                raw = None
        try:
            self.session = Session.model_validate_json(raw) if raw else None
        except ValueError:  # обрезанный файл сессии — просто войти заново
            quarantine(self._session_file(), "сессия повреждена")
            self.session = None
        return self.session

    # запросы
    def _headers(self) -> dict:
        return {"apikey": self.config.publishable_key, "Content-Type": "application/json"}

    def _token_request(self, grant: str, body: dict) -> Session:
        try:
            r = self.http.post(f"{self.config.url}/auth/v1/token", params={"grant_type": grant}, json=body,
                               headers=self._headers())
        except httpx.TransportError as e:
            raise CloudError("Нет связи с облаком. Проверь интернет.", str(e)) from e
        if r.status_code >= 400:
            raise _error_from(r)
        d = r.json()
        user = d.get("user") or {}
        self.session = Session(access_token=d["access_token"], refresh_token=d["refresh_token"],
                               expires_at=int(d.get("expires_at") or time.time() + int(d.get("expires_in", 3600))),
                               user_id=user.get("id", ""), email=user.get("email", ""))
        self._save()
        return self.session

    def sign_in(self, email: str, password: str) -> Session:
        return self._token_request("password", {"email": email.strip(), "password": password})

    def sign_up(self, email: str, password: str, name: str = "") -> str:
        """Регистрация. Возвращает подсказку, что делать дальше."""
        try:
            r = self.http.post(f"{self.config.url}/auth/v1/signup", headers=self._headers(),
                               json={"email": email.strip(), "password": password, "data": {"name": name.strip()}})
        except httpx.TransportError as e:
            raise CloudError("Нет связи с облаком. Проверь интернет.", str(e)) from e
        if r.status_code >= 400:
            raise _error_from(r)
        d = r.json()
        if d.get("access_token"):
            self._token_request("password", {"email": email.strip(), "password": password})
            return "Аккаунт создан."
        return "Аккаунт создан. Открой письмо от Supabase и подтверди почту, потом войди."

    def refresh(self) -> Session:
        if not self.session:
            raise CloudError("Нужно войти в аккаунт.")
        return self._token_request("refresh_token", {"refresh_token": self.session.refresh_token})

    def access_token(self) -> str:
        with self._lock:
            if not self.session:
                self.load_session()
            if not self.session:
                raise CloudError("Нужно войти в аккаунт.")
            if self.session.expires_at - time.time() < 120:
                self.refresh()
            return self.session.access_token

    @property
    def user_id(self) -> str:
        if not self.session:
            self.load_session()
        if not self.session:
            raise CloudError("Нужно войти в аккаунт.")
        return self.session.user_id

    def sign_out(self) -> None:
        if self.session:
            try:
                self.http.post(f"{self.config.url}/auth/v1/logout",
                               headers={**self._headers(), "Authorization": f"Bearer {self.session.access_token}"})
            except httpx.TransportError:
                pass
        self.session = None
        self._save()

    def to_json(self) -> str:
        return json.dumps(self.session.model_dump() if self.session else None)
