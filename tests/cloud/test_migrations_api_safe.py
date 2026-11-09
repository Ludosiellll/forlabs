"""Supabase отклоняет UPDATE и DELETE без WHERE в запросах через API (расширение safeupdate) — даже внутри функций.
Такая функция работает из SQL Editor, а вызванная сервером молча ничего не сохраняет: так не сохранялся код ссылки
Telegram, и бот отвечал «Ссылка устарела»."""

import re
from pathlib import Path

MIGRATIONS = sorted((Path(__file__).parents[2] / "supabase" / "migrations").glob("*.sql"))
FUNCTION = re.compile(r"create\s+or\s+replace\s+function\s+([\w.]+)\s*\(.*?\$\$(.*?)\$\$", re.I | re.S)
WRITE = re.compile(r"\b(update\s+[\w.]+\s+set\b[^;]*|delete\s+from\s+[\w.]+[^;]*)", re.I | re.S)


def final_bodies() -> dict[str, tuple[str, str]]:
    """Последнее определение каждой функции: имя → (файл, тело)."""
    out: dict[str, tuple[str, str]] = {}
    for f in MIGRATIONS:
        for m in FUNCTION.finditer(f.read_text(encoding="utf-8")):
            out[m.group(1).lower()] = (f.name, m.group(2))
    return out


def test_no_function_updates_or_deletes_without_where():
    bad = []
    for name, (file, body) in final_bodies().items():
        code = re.sub(r"--[^\n]*", "", body)
        bad += [f"{file}: {name}: {' '.join(s.split())[:90]}" for s in WRITE.findall(code)
                if not re.search(r"\bwhere\b", s, re.I)]
    assert not bad, "UPDATE/DELETE без WHERE (через API не выполнится):\n" + "\n".join(bad)


def test_support_link_code_is_saved_by_a_statement_with_where():
    file, body = final_bodies()["public.support_config_set"]
    assert re.search(r"update\s+private\.support_config\s+set\b.*\bwhere\s+id\b", body, re.I | re.S), file


def test_app_says_what_to_do_when_server_did_not_save_the_link():
    import httpx
    import pytest

    from forlabs.cloud.auth import CloudError
    from forlabs.cloud.support import Support

    class Http:
        def post(self, url, **kw):
            return httpx.Response(500, json={"error": "link not saved: UPDATE requires a WHERE clause"},
                                  request=httpx.Request("POST", url))

    class Store:
        http = Http()
        cfg = type("Cfg", (), {"url": "https://example.supabase.co"})()

        def _h(self):
            return {}

        def _check(self, r):
            r.raise_for_status()

    with pytest.raises(CloudError) as e:
        Support(Store()).setup()
    assert "0012_support_link_saved.sql" in e.value.message_ru and "SQL Editor" in e.value.message_ru
