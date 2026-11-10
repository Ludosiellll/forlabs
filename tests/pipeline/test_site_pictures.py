"""Сайт в лабе: картинок у ForLabs нет — он кладёт заглушки и говорит об этом; картинку, которую студент положил
на то же место, пересборка не затирает; ссылки на страницы, которых нет, ведут в «#»."""

import io

from PIL import Image
from test_run import ANS, FIXTURE, RAW, setup

from forlabs.runner.shots import FakeShooter

HTML = ('<!doctype html>\n<html lang="ru">\n<head><meta charset="utf-8"><link rel="stylesheet" href="style.css">'
        '</head>\n<body>\n<nav><a href="index.html">Главная</a> <a href="menu.html">Меню</a></nav>\n'
        '<img src="slide-latte.jpg" alt="Латте">\n</body>\n</html>\n')
CSS = '.hero { background: url("coffee-bg.jpg") center / cover fixed; }\n'
SEC = {"blocks": [
    {"type": "paragraph", "text": "Слайдер показывает фотографии напитков."},
    {"type": "listing", "topic": "Исходный код страницы index.html", "language": "html", "code": HTML.strip()},
    {"type": "figure", "kind": "screenshot", "topic": "Страница кофейни в окне браузера",
     "instruction": "Открой index.html в браузере"},
]}
SEC2 = {"blocks": [{"type": "paragraph", "text": "Фон шапки закреплён при прокрутке."}]}
PLAN = {"language": "web", "kind": "web", "name": "Кофейня", "entry": "index.html",
        "files": [{"path": "index.html", "content": HTML}, {"path": "style.css", "content": CSS}],
        "listings": [{"section": 1, "block": 2, "file": "index.html"}],
        "shots": [{"section": 1, "block": 3, "mode": "browser", "url": "index.html", "expect": ""}]}


def run(tmp_home, tmp_path, monkeypatch, extra=()):
    from forlabs.runner import languages

    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS, PLAN, *extra])
    pipe.projects, pipe.shooter = True, FakeShooter()
    monkeypatch.setattr(languages, "detect", lambda *a, **k: {"edge": languages.Toolchain("edge", "msedge.exe")})
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    return pipe, st, tmp_path / "сдача" / "ПССИП" / "ЛР7" / "Проект Кофейня"


def test_site_gets_placeholder_pictures_and_says_so(tmp_home, tmp_path, monkeypatch):
    pipe, st, out = run(tmp_home, tmp_path, monkeypatch)
    for name in ("slide-latte.jpg", "coffee-bg.jpg"):
        with Image.open(out / name) as im:
            im.load()
    assert st.project.placeholders == ["coffee-bg.jpg", "slide-latte.jpg"]
    said = [e.text for e in pipe.journal(st)]
    assert any("заглушк" in t and "slide-latte.jpg" in t for t in said)


def test_dead_menu_link_leads_to_hash_in_the_project_and_in_the_listing(tmp_home, tmp_path, monkeypatch):
    pipe, st, out = run(tmp_home, tmp_path, monkeypatch)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert '<a href="#">Меню</a>' in html and '<a href="index.html">Главная</a>' in html
    listing = next(b for s in st.report.sections for b in s.blocks if b.type == "listing")
    assert 'href="#">Меню' in listing.code and "menu.html" not in listing.code


def test_students_own_picture_is_not_replaced_by_a_placeholder(tmp_home, tmp_path, monkeypatch):
    pipe, st, out = run(tmp_home, tmp_path, monkeypatch)
    real = io.BytesIO()
    Image.new("RGB", (640, 360), (10, 120, 60)).save(real, "JPEG")
    (out / "slide-latte.jpg").write_bytes(real.getvalue())  # студент положил свою фотографию
    st = pipe.retry_project(st)
    assert (out / "slide-latte.jpg").read_bytes() == real.getvalue()
    assert not (out / "slide-latte.jpg.bak").exists()
    assert st.project.placeholders == ["coffee-bg.jpg"]  # своей картинки студента в списке заглушек больше нет
