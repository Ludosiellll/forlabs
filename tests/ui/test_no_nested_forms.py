"""Форма внутри формы в HTML не бывает: браузер молча выбрасывает внутренний тег <form>, и её кнопки перестают
работать (так на странице настроек сломался блок «Программы для лаб»). Ни на одной странице такого быть не должно."""

from html.parser import HTMLParser

from fastapi.testclient import TestClient
from test_appshots_ui import lab

from forlabs.core.llm import FakeLLM
from forlabs.core.settings import Settings
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext


class Forms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth, self.nested = 0, []

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            if self.depth:
                self.nested.append(dict(attrs).get("action") or dict(attrs).get("class") or "форма")
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "form":
            self.depth = max(0, self.depth - 1)


def nested(html: str) -> list[str]:
    p = Forms()
    p.feed(html)
    return p.nested


def test_parser_sees_a_nested_form():
    assert nested('<form action="/a"><form class="inner"></form></form>') == ["inner"]
    assert nested('<form action="/a"></form><form action="/b"></form>') == []


def test_no_page_puts_a_form_inside_a_form(tmp_home, tmp_path, monkeypatch):
    ctx = AppContext(settings=Settings(mode="local", tour_done=True), llm_override=lambda: FakeLLM([]),
                     prefer_word=False, render_diagrams=False, synchronous=True)
    ctx.pick = lambda kind, initial="": None
    c = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    for path in ("/", "/settings", "/profiles", "/profiles/new", "/labs/new", "/semester", "/rules"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert nested(r.text) == [], f"{path}: форма внутри формы — {nested(r.text)}"
    c2, ctx2, url = lab(tmp_home, tmp_path, monkeypatch)
    for tab in ("report", "parse", "journal"):
        assert nested(c2.get(f"{url}?tab={tab}").text) == [], tab


def test_tool_rows_on_the_settings_page_keep_their_link_form(tmp_home):
    ctx = AppContext(settings=Settings(mode="local", tour_done=True), llm_override=lambda: FakeLLM([]),
                     prefer_word=False, render_diagrams=False, synchronous=True)
    page = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765").get("/settings").text
    assert page.count("data-tool-link hidden") == page.count('class="tool" data-tool=') > 0
    assert 'form="settings-form"' in page  # поля «Дополнительно» по-прежнему уходят с настройками
