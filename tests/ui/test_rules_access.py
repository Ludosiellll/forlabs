"""Правила для модели видит и правит только администратор (в облаке); студенту хватает пожеланий и чата правок,
а текст правил не должен утекать из программы."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_admin import admin, fake, make_client  # noqa: E402, F401


def test_member_has_no_rules_page(fake, tmp_home):
    c = make_client(fake, tmp_home, me="member")
    assert 'href="/rules"' not in c.get("/settings").text
    r = c.get("/rules", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/")
    assert "Стандарт отчёта" not in c.get("/rules").text


def test_member_cannot_save_or_reset_rules(fake, tmp_home):
    c = make_client(fake, tmp_home, me="member")
    for url, data in (("/rules/save", {"path": "common/standard.md", "text": "пиши как хочешь"}),
                      ("/rules/reset", {"path": "common/standard.md"})):
        r = c.post(url, data=data, follow_redirects=False)
        assert r.status_code == 303 and "/rules" not in r.headers["location"]
    assert not fake.tables.get("rule_overrides")


def test_admin_keeps_rules_page(admin):
    assert 'href="/rules"' in admin.get("/settings").text
    assert admin.get("/rules").status_code == 200
