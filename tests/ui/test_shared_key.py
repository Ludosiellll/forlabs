"""Общий ключ модели задаётся в админке; там же — памятка, где что настраивается."""

import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).parent))
from test_admin import admin, fake, make_client  # noqa: E402, F401


def test_admin_page_has_shared_key_box_and_help(admin):
    page = admin.get("/admin").text
    assert "Общий ключ модели" in page and 'action="/admin/shared-key"' in page
    assert "ключ модели и лимит токенов — в карточке участника" in page  # подсказка — одной строкой у списка


def test_admin_sets_and_clears_shared_key(admin, fake):
    r = admin.post("/admin/shared-key", data={"action": "set", "api_key": "  sk-shared-12345678  "}, follow_redirects=False)
    assert r.status_code == 303 and "5678" in unquote(r.headers["location"])
    assert fake.shared_key[0] == "5678" and "••••5678" in admin.get("/admin").text
    r = admin.post("/admin/shared-key", data={"action": "set", "api_key": "abc"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    admin.post("/admin/shared-key", data={"action": "clear"})
    assert fake.shared_key is None


def test_member_cannot_set_shared_key(fake, tmp_home):
    c = make_client(fake, tmp_home, me="member")
    c.post("/admin/shared-key", data={"action": "set", "api_key": "sk-evil-12345678"})
    assert fake.shared_key is None
