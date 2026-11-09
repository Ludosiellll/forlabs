"""Настройки приложения. Ключ API хранится отдельно: в хранилище паролей системы или в переменной окружения."""

from __future__ import annotations

import json
import os
import sys
from typing import Literal

from pydantic import BaseModel, field_validator

from forlabs.core import paths
from forlabs.core.fs import atomic_write_text, quarantine

KEYRING_SERVICE = "ForLabs"
KEYRING_USER = "api_key"

DEFAULT_MODEL = "deepseek-v4.1-flash-1017"
# если ключ не пускает модель — следующая по списку (ключ участника может отличаться от ключа администратора)
MODEL_FALLBACKS = {"deepseek-v4.1-flash-1017": ["deepseek-v4.1-flash"]}
MODEL_CHOICES = ["deepseek-v4.1-flash-1017", "deepseek-v4-flash"]
_OLD_DEFAULTS = {"deepseek-v4.1-flash"}


API_PRESETS: dict[str, dict[str, object]] = {
    "aikeys": {
        "title": "AI API Shop",
        "base_url": "https://api.aikeysforyou.com/v1",
        "models": MODEL_CHOICES,
    },
    "deepseek": {
        "title": "DeepSeek напрямую",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-v4-flash", "deepseek-chat"],
    },
}


class Settings(BaseModel):
    mode: Literal["cloud", "local"] = "cloud"
    base_url: str = "https://api.aikeysforyou.com/v1"
    model: str = DEFAULT_MODEL
    api_key_source: Literal["keyring", "env"] = "keyring"
    word_path: str | None = None
    soffice_path: str | None = None
    tesseract_path: str | None = None
    temperature: float = 0.4
    browser_channel: str | None = None
    tour_done: bool = False  # обучение при первом запуске пройдено или пропущено

    @field_validator("model")
    @classmethod
    def _latest_by_default(cls, v: str) -> str:
        """Старое значение по умолчанию поднимается до новейшей DeepSeek v4.1 flash."""
        return DEFAULT_MODEL if (v or "").strip() in _OLD_DEFAULTS or not (v or "").strip() else v.strip()


def _settings_file():
    return paths.data_home() / "settings.json"


SETTINGS_WERE_RESET = False  # файл настроек был повреждён и заменён настройками по умолчанию


def load_settings() -> Settings:
    f = _settings_file()
    if f.exists():
        try:
            return Settings.model_validate_json(f.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as e:  # обрезан при выключении — настройки по умолчанию
            quarantine(f, str(e)[:120])
            global SETTINGS_WERE_RESET
            SETTINGS_WERE_RESET = True
    return Settings()


def save_settings(s: Settings) -> None:
    f = _settings_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(f, json.dumps(s.model_dump(), ensure_ascii=False, indent=2))


def use_windows_vault() -> None:
    """Собранная программа не видит метаданные пакетов, по которым keyring ищет хранилище, — указываем явно
    «Диспетчер учётных данных» Windows (ключ API и вход в облако)."""
    if sys.platform != "win32":
        return
    try:
        import keyring
        from keyring.backends.Windows import WinVaultKeyring

        if type(keyring.get_keyring()).__name__ != "WinVaultKeyring":
            keyring.set_keyring(WinVaultKeyring())
    except Exception:
        pass


def _keyring_get() -> str | None:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        return None


def _keyring_set(key: str) -> bool:
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
        return True
    except Exception:
        return False


def get_api_key() -> str | None:
    return _keyring_get() or os.environ.get("FORLABS_API_KEY") or None


def set_api_key(key: str) -> bool:
    """Сохраняет ключ в хранилище паролей. Возвращает False, если хранилище недоступно."""
    return _keyring_set(key.strip())
