"""Настройки приложения. Ключ API хранится отдельно: в хранилище паролей системы или в переменной окружения."""

from __future__ import annotations

import json
import os
from typing import Literal

from pydantic import BaseModel

from forlabs.core import paths

KEYRING_SERVICE = "ForLabs"
KEYRING_USER = "api_key"

API_PRESETS: dict[str, dict[str, object]] = {
    "aikeys": {
        "title": "AI API Shop",
        "base_url": "https://api.aikeysforyou.com/v1",
        "models": ["deepseek-v4.1-flash", "deepseek-v4-flash"],
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
    model: str = "deepseek-v4.1-flash"
    api_key_source: Literal["keyring", "env"] = "keyring"
    word_path: str | None = None
    soffice_path: str | None = None
    tesseract_path: str | None = None
    temperature: float = 0.4
    browser_channel: str | None = None


def _settings_file():
    return paths.data_home() / "settings.json"


def load_settings() -> Settings:
    f = _settings_file()
    if f.exists():
        return Settings.model_validate_json(f.read_text(encoding="utf-8"))
    return Settings()


def save_settings(s: Settings) -> None:
    f = _settings_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(s.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")


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
