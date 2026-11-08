"""Лимиты для всех: токены в день (свой в карточке, по умолчанию 1 000 000) и запросы на лабу — сообщения и админка."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_admin import M, admin, fake  # noqa: E402, F401
from test_trial import _llm_error  # noqa: E402


def test_limit_messages_are_clear_and_not_retried():
    e = _llm_error(403, "daily token limit: 1000000")
    assert "дневной лимит" in e.message_ru.lower() and "1 000 000" in e.message_ru and "завтра" in e.message_ru
    assert not e.retryable
    e = _llm_error(403, "lab calls limit: 300")
    assert "300" in e.message_ru and "поддержк" in e.message_ru and not e.retryable


def test_admin_sets_and_clears_daily_limit(admin, fake):
    row = fake.tables["members"][1]
    r = admin.post(f"/admin/u/{M}/access", data={"action": "daily", "daily": "2 500 000"}, follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"] and row["daily_tokens"] == 2_500_000
    admin.post(f"/admin/u/{M}/access", data={"action": "daily", "daily": ""})
    assert row["daily_tokens"] is None
    for bad in ("0", "500", "много"):
        r = admin.post(f"/admin/u/{M}/access", data={"action": "daily", "daily": bad}, follow_redirects=False)
        assert "err=" in r.headers["location"], bad


def test_card_shows_daily_limit_and_today_usage(admin, fake):
    page = admin.get(f"/admin/u/{M}").text
    assert 'name="daily"' in page and "1 000 000" in page and "сегодня" in page
