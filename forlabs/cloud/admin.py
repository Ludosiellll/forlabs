"""Данные админ-панели: что администратор видит об участниках."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from forlabs.core.models import Profile

# Беларусь живёт по UTC+3 без перехода на летнее время; tzdata на Windows не нужен
MINSK = timezone(timedelta(hours=3))
EXPIRING_DAYS = 3


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def end_of_day_minsk(day: str) -> str:
    """«2026-12-12» → конец этого дня по Минску в ISO."""
    d = datetime.strptime(day, "%Y-%m-%d")
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=MINSK).isoformat()


class KeyInfo(BaseModel):
    user_id: str
    last4: str
    set_at: str | None = None
    model: str | None = None  # модель этого ключа (миграция 0014); None — модель по умолчанию


class LabRow(BaseModel):
    owner: str
    profile_id: str
    subject_id: str
    number: str
    status: str
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def key(self) -> str:
        return f"{self.profile_id}/{self.subject_id}/{self.number}"


class MemberEvent(BaseModel):
    id: int
    user_id: str
    actor: str | None = None
    kind: str
    detail: dict = Field(default_factory=dict)
    created_at: str | None = None


def access_state(approved: bool, role: str, access_until: str | None, now: datetime | None = None) -> str:
    """pending — не одобрен; unlimited — без срока или администратор; expired; expiring (≤3 дней); active."""
    if not approved:
        return "pending"
    if role == "admin":
        return "unlimited"
    until = parse_ts(access_until)
    if until is None:
        return "unlimited"
    now = now or now_utc()
    if until <= now:
        return "expired"
    if until - now <= timedelta(days=EXPIRING_DAYS):
        return "expiring"
    return "active"


def days_left(access_until: str | None, now: datetime | None = None) -> int | None:
    until = parse_ts(access_until)
    if until is None:
        return None
    seconds = (until - (now or now_utc())).total_seconds()
    return max(0, math.ceil(seconds / 86400))


class AdminOverview(BaseModel):
    members: list = Field(default_factory=list)  # list[Member]
    keys: dict[str, KeyInfo] = Field(default_factory=dict)
    usage_30d: dict[str, int] = Field(default_factory=dict)
    daily: list[tuple[str, str, int]] = Field(default_factory=list)  # (день, user_id, токены)
    labs: list[LabRow] = Field(default_factory=list)
    profiles: dict[str, list[Profile]] = Field(default_factory=dict)


class MemberCard(BaseModel):
    member: object
    key: KeyInfo | None = None
    profiles: list[Profile] = Field(default_factory=list)
    labs: list[LabRow] = Field(default_factory=list)
    usage_by_lab: dict[str, tuple[int, int]] = Field(default_factory=dict)  # лаба → (токены, запросы)
    daily: list[tuple[str, int]] = Field(default_factory=list)
    events: list[MemberEvent] = Field(default_factory=list)
