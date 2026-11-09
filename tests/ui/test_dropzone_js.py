"""Выбор методички в браузере: список файлов, крестик убирает один, «Убрать все» — все; из окна выбора и
перетаскиванием; с клавиатуры. Отправляется ровно то, что осталось в списке."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from test_routes import make_profile
from test_several_labs_routes import NAMES, P, S, TOPICS, as_docx, lab_text, raw

from forlabs.core.llm import FakeLLM
from forlabs.core.settings import Settings
from forlabs.executors.diagrams import browser_available
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

STATIC = Path(__file__).parents[2] / "forlabs" / "ui" / "static"
pytestmark = pytest.mark.skipif(not browser_available(), reason="нет браузера")


def docx_file(n):
    return {"name": NAMES[n], "mimeType": "application/octet-stream", "buffer": as_docx(lab_text(n, TOPICS[n]))}


@pytest.fixture
def page(tmp_home, tmp_path):
    from playwright.sync_api import sync_playwright

    from forlabs.executors.diagrams import _launch

    fake = FakeLLM([raw("19"), raw("21")])
    ctx = AppContext(settings=Settings(mode="local", tour_done=True), llm_override=lambda: fake, prefer_word=False,
                     render_diagrams=False, synchronous=True)
    client = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(client, tmp_path)

    def serve(route):  # браузер ходит в приложение через TestClient: без сервера и порта
        req = route.request
        parts = urlsplit(req.url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        if parts.path.startswith("/static/"):
            return route.fulfill(path=str(STATIC / parts.path.removeprefix("/static/")))
        headers = {"content-type": req.headers["content-type"]} if req.method == "POST" else {}
        r = client.request(req.method, path, content=req.post_data_buffer, headers=headers, follow_redirects=False)
        keep = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "location")}
        route.fulfill(status=r.status_code, headers=keep, body=r.content)

    with sync_playwright() as pw:
        browser = _launch(pw)
        pg = browser.new_page()
        pg.set_default_timeout(5000)
        pg.route("http://forlabs.test/**", serve)
        pg.goto(f"http://forlabs.test/labs/new?profile={P}&subject={S}")
        pg.ctx = ctx
        yield pg
        browser.close()


def picked(pg):
    return pg.eval_on_selector("[data-dropzone] input[type=file]", "i => Array.from(i.files).map(f => f.name)")


def listed(pg):
    return pg.locator("[data-picked] li .pick-name").all_inner_texts()


def drop(pg, *names):
    dt = pg.evaluate_handle("""names => { const dt = new DataTransfer();
        names.forEach(n => dt.items.add(new File(['x'], n))); return dt; }""", list(names))
    pg.dispatch_event("[data-dropzone]", "drop", {"dataTransfer": dt})


def test_pick_remove_drop_and_submit(page):
    zone_input = page.locator("[data-dropzone] input[type=file]")
    zone_input.set_input_files([docx_file("19"), docx_file("20")])
    zone_input.set_input_files([docx_file("21")])  # второй выбор добавляет, а не заменяет
    assert listed(page) == [NAMES["19"], NAMES["20"], NAMES["21"]] == picked(page)
    assert page.locator("[data-picked-many]").is_visible()

    page.get_by_role("button", name=f"Убрать «{NAMES['20']}»").click()
    assert picked(page) == [NAMES["19"], NAMES["21"]] == listed(page)

    drop(page, "старая методичка.doc", NAMES["19"])  # .doc не читается, повтор не добавляется
    assert picked(page) == [NAMES["19"], NAMES["21"]]
    assert ".docx" in page.locator("[data-dropzone] .pick-note").inner_text()

    page.get_by_role("button", name="Разобрать методичку").click()
    page.wait_for_url(lambda url: "msg=" in url)
    assert "ЛР 19, ЛР 21" in page.content()
    labs = {lab.number: lab for lab in page.ctx.store.list_labs(P, S)}
    assert sorted(labs) == ["19", "21"]


def test_keyboard_and_clear_all(page):
    zone_input = page.locator("[data-dropzone] input[type=file]")
    zone_input.set_input_files([docx_file("19"), docx_file("20"), docx_file("21")])
    page.get_by_role("button", name=f"Убрать «{NAMES['19']}»").focus()
    page.keyboard.press("Enter")
    assert picked(page) == [NAMES["20"], NAMES["21"]]
    # фокус не теряется: он на крестике следующего файла
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == f"Убрать «{NAMES['20']}»"
    page.keyboard.press("Space")
    assert picked(page) == [NAMES["21"]]
    assert not page.locator("[data-picked-many]").is_visible()

    drop(page, "ЛР22.docx")
    page.get_by_role("button", name="Убрать все").click()
    assert picked(page) == [] and listed(page) == []
    assert page.evaluate("document.activeElement.type") == "file"
