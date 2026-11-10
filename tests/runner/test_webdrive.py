"""Шаги на странице сайта: наведение, прокрутка, поля. Проверяется в настоящем браузере (Chromium Playwright)."""

import pytest

from forlabs.runner import webdrive

PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><style>
body { margin: 0; font: 16px sans-serif; }
nav li.drop ul { display: none; }
nav li.drop:hover ul { display: block; }
.btn { background: rgb(200, 200, 200); }
.btn:hover { background: rgb(10, 120, 60); }
.spacer { height: 2400px; }
</style></head><body>
<nav><ul><li class="drop"><a href="#">Меню</a><ul><li>Десерты</li><li>Напитки</li></ul></li></ul></nav>
<h1 id="title">Кофейня «Зерно»</h1>
<button class="btn" id="order">Заказать</button>
<form><input id="guests" type="number"><select id="hall"><option>Зал</option><option>Терраса</option></select>
<label><input id="agree" type="checkbox"> Согласен</label></form>
<img id="pic" src="no-such-file.jpg" alt="Латте">
<div class="spacer"></div>
<section id="slider"><h2>Латте с корицей</h2></section>
</body></html>
"""


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright

        from forlabs.executors.diagrams import _launch

        pw = sync_playwright().start()
        b = _launch(pw)  # Edge в Windows, иначе браузер Playwright или найденный Chromium
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"нет браузера для Playwright: {e}")
    yield b
    b.close()
    pw.stop()


@pytest.fixture
def page(browser, tmp_path):
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "about.html").write_text("<meta charset='utf-8'><h1>О нас</h1>", encoding="utf-8")
    pg = browser.new_page(viewport={"width": 1200, "height": 700})
    pg.goto((tmp_path / "index.html").as_uri())
    yield pg
    pg.close()


def step(action, target="", value=""):
    return {"action": action, "target": target, "value": value}


def test_hover_opens_the_dropdown_and_it_is_seen(page):
    assert "Десерты" not in webdrive.page_state(page)
    webdrive.perform(page, [step("hover", "Меню")])
    assert "Десерты" in webdrive.page_state(page)


def test_hover_changes_the_button_by_css_selector(page):
    webdrive.perform(page, [step("hover", ".btn")])
    assert page.evaluate("getComputedStyle(document.querySelector('.btn')).backgroundColor") == "rgb(10, 120, 60)"
    assert "Под курсором: Заказать" in webdrive.page_state(page)


def test_text_below_the_fold_is_not_seen_until_scrolled_to(page):
    assert "Латте с корицей" not in webdrive.page_state(page)  # раньше текст за экраном засчитывался как видимый
    webdrive.perform(page, [step("scroll", "id:slider")])
    assert "Латте с корицей" in webdrive.page_state(page)


def test_scroll_by_pixels_and_reveal_of_the_expected_text(page):
    webdrive.perform(page, [step("scroll", "", "500")])
    assert page.evaluate("scrollY") == 500
    assert webdrive.reveal(page, "Латте с корицей") is True
    assert "Латте с корицей" in webdrive.page_state(page)
    assert webdrive.reveal(page, "Такого текста нет") is False


def test_form_steps_by_id(page):
    webdrive.perform(page, [step("set", "id:guests", "4"), step("select", "id:hall", "Терраса"),
                            step("check", "agree"), step("click", "Заказать")])
    assert page.input_value("#guests") == "4" and page.input_value("#hall") == "Терраса"
    assert page.is_checked("#agree")
    assert "guests = 4" in webdrive.page_state(page)


def test_missing_element_is_a_clear_reason(page):
    with pytest.raises(webdrive.StepError) as e:
        webdrive.perform(page, [step("hover", "Контакты")], timeout=1.0)
    assert "Контакты" in str(e.value)


def test_broken_picture_is_reported(page):
    assert "no-such-file.jpg" in webdrive.page_state(page) and "не загрузил" in webdrive.page_state(page)


def test_open_goes_to_another_page_of_the_site(page):
    webdrive.perform(page, [step("open", "", "about.html")])
    assert page.url.endswith("/about.html") and "О нас" in webdrive.page_state(page)


def test_open_outside_the_site_is_refused(page):
    with pytest.raises(webdrive.StepError):
        webdrive.perform(page, [step("open", "", "file:///etc/passwd")])
    with pytest.raises(webdrive.StepError):
        webdrive.perform(page, [step("open", "", "../secret.html")])


def test_attach_to_a_browser_started_with_a_debugging_port(tmp_path):
    """Как в Windows: браузер запускает ForLabs (окно снимается как обычно), а шаги идут через порт отладки."""
    import socket
    import subprocess
    import time
    from pathlib import Path

    from forlabs.executors.diagrams import FALLBACK_EXECUTABLES

    exe = next((x for x in FALLBACK_EXECUTABLES if Path(x).exists()), None)
    if exe is None:
        pytest.skip("нет Chromium для запуска с портом отладки")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    url = (tmp_path / "index.html").as_uri()
    proc = subprocess.Popen([exe, "--headless=new", "--no-sandbox", f"--user-data-dir={tmp_path / 'profile'}",
                             f"--remote-debugging-port={port}", "--window-size=1200,700", url],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    got: dict = {}

    def drive():  # в своём потоке: в потоке теста уже работает Playwright общей фикстуры, второй там не запустить
        for _ in range(40):  # браузер поднимает порт не сразу
            with webdrive.attach(port, url, timeout=2.0) as page:
                if page is not None:
                    webdrive.perform(page, [step("hover", "Меню")])
                    got["state"] = webdrive.page_state(page)
                    return
            time.sleep(0.25)

    try:
        import threading

        th = threading.Thread(target=drive)
        th.start()
        th.join(60)
        assert "Десерты" in got.get("state", ""), "к браузеру подключиться не удалось"
    finally:
        proc.kill()


def test_attach_to_nothing_gives_no_page():
    import threading

    got = []

    def drive():
        with webdrive.attach(1, "file:///x.html", timeout=0.5) as page:
            got.append(page)

    th = threading.Thread(target=drive)
    th.start()
    th.join(30)
    assert got == [None]


# --- правила сценариев и запуск Edge

def test_hover_is_a_browser_step_only():
    from forlabs.runner.plan import ProjectPlan, validate

    def site(mode, step_):
        return ProjectPlan.model_validate({
            "language": "web", "kind": "web", "name": "Сайт", "entry": "index.html",
            "files": [{"path": "index.html", "content": PAGE}], "listings": [],
            "shots": [{"section": 1, "block": 2, "mode": mode, "url": "index.html", "steps": [step_]}]})

    assert validate(site("browser", step("hover", ".btn"))) == []
    assert validate(site("browser", step("scroll", "id:slider"))) == []
    assert validate(site("browser", step("scroll", "", "600"))) == []
    assert any("hover" in p for p in validate(site("browser", step("hover"))))  # без элемента наводить не на что
    assert any("hover" in p for p in validate(site("window", step("hover", ".btn"))))  # в окне программы шага нет


def test_edge_gets_a_debugging_port_only_when_asked(tmp_path):
    from forlabs.runner.shots import edge_command

    plain = edge_command("msedge.exe", tmp_path, "file:///x/index.html")
    assert "remote-debugging-port" not in plain
    cmd = edge_command("msedge.exe", tmp_path, "file:///x/index.html", port=9333)
    assert "--remote-debugging-port=9333" in cmd and cmd.rstrip().endswith("file:///x/index.html")


def test_steps_that_need_the_page_driver():
    from forlabs.runner.shots import needs_page_driver

    assert needs_page_driver([step("hover", ".btn")]) and needs_page_driver([step("scroll", "", "300")])
    assert needs_page_driver([step("open", "", "about.html")])
    assert not needs_page_driver([step("set", "id:a", "1"), step("click", "OK")]) and not needs_page_driver([])
