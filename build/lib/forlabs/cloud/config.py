"""Публичные параметры облака. Публикуемый ключ безопасно встраивать: доступ закрывают политики RLS."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CloudConfig:
    url: str
    publishable_key: str

    @property
    def llm_base_url(self) -> str:
        return f"{self.url}/functions/v1/llm"


DEFAULT = CloudConfig(
    url=os.environ.get("FORLABS_SUPABASE_URL", "https://iebwndcikwnnvjvvdcyu.supabase.co"),
    publishable_key=os.environ.get("FORLABS_SUPABASE_KEY", "sb_publishable_sGvIQcI4K750Up9b7NrT3A_a2YAaGzl"),
)
