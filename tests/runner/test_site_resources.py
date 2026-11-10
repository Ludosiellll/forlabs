"""Сайт ссылается на файлы, которых нет в проекте: картинки ForLabs кладёт сам (заглушки), ссылки на страницы,
которых нет, ведут в «#», а не на «Файл не найден»."""

import io

from PIL import Image

from forlabs.runner import languages as L
from forlabs.runner.plan import ProjectPlan, fix_dead_links, missing_images, missing_pages, validate

INDEX = """<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><link rel="stylesheet" href="style.css"><link rel="icon" href="img/cup-icon.png"></head>
<body>
<nav><a href="index.html">Главная</a> <a href="menu.html">Меню</a> <a href='pages/about.html#team'>О нас</a>
<a href="#top">Вверх</a> <a href="https://example.com/x.html">Сайт</a> <a href="mailto:a@b.ru">Почта</a></nav>
<img src="slide-latte.jpg" alt="Латте"> <img src="./slide-latte.jpg" alt="ещё раз">
<img src="https://example.com/pic.jpg" alt="внешняя"> <img src="data:image/png;base64,AAAA" alt="встроенная">
<img src="logo.svg" alt="эмблема">
</body></html>
"""
CSS = """.hero { background: url("img/coffee-bg.jpg") center / cover fixed; }
.btn::after { content: url(cup-icon.png); }
.x { background-image: url('https://example.com/a.png'); }
"""


def site(files=None, shots=None):
    files = files or [("index.html", INDEX), ("style.css", CSS), ("logo.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>")]
    return ProjectPlan.model_validate({
        "language": "web", "kind": "web", "name": "Кофейня", "entry": "index.html",
        "files": [{"path": p, "content": c} for p, c in files],
        "listings": [{"section": 1, "block": 1, "file": "index.html"}], "shots": shots or []})


def test_missing_images_are_found_from_html_and_css_relative_to_their_file():
    assert missing_images(site()) == ["cup-icon.png", "img/coffee-bg.jpg", "img/cup-icon.png", "slide-latte.jpg"]


def test_images_that_exist_or_are_external_are_not_missing():
    got = missing_images(site())
    assert "logo.svg" not in got and not any("example.com" in x or x.startswith("data:") for x in got)


def test_missing_pages_are_only_local_documents():
    assert missing_pages(site()) == ["menu.html", "pages/about.html"]


def test_dead_page_links_become_hash_and_the_rest_is_untouched():
    fixed = fix_dead_links(site())
    html = fixed.file("index.html").content
    assert '<a href="#">Меню</a>' in html and "<a href='#'>О нас</a>" in html
    assert '<a href="index.html">Главная</a>' in html and 'href="style.css"' in html
    assert 'href="https://example.com/x.html"' in html and 'href="#top"' in html
    assert missing_pages(fixed) == []


def test_plan_with_missing_images_is_still_valid():
    # картинки не текст: модель их дать не может, их кладёт ForLabs — план из-за них не отклоняется
    assert validate(site()) == []


def test_materialize_puts_placeholder_images_next_to_the_site(tmp_path):
    d = tmp_path / "site"
    L.materialize(site(), d)
    for rel in ("slide-latte.jpg", "img/coffee-bg.jpg", "img/cup-icon.png", "cup-icon.png"):
        with Image.open(d / rel) as im:
            im.load()
            assert im.width >= 64 and im.height >= 64
    with Image.open(d / "img" / "coffee-bg.jpg") as bg, Image.open(d / "img" / "cup-icon.png") as icon:
        assert bg.format == "JPEG" and icon.format == "PNG"
        assert bg.width > icon.width  # фон крупнее значка


def test_placeholders_do_not_overwrite_real_files(tmp_path):
    d = tmp_path / "site"
    d.mkdir()
    real = io.BytesIO()
    Image.new("RGB", (10, 10), (1, 2, 3)).save(real, "JPEG")
    (d / "slide-latte.jpg").write_bytes(real.getvalue())
    made = L.write_placeholders(site(), d)
    assert "slide-latte.jpg" not in made and (d / "slide-latte.jpg").read_bytes() == real.getvalue()
    assert "img/coffee-bg.jpg" in made


def test_php_site_and_non_sites():
    php = ProjectPlan.model_validate({
        "language": "php", "kind": "web", "name": "Сайт", "entry": "index.php",
        "files": [{"path": "index.php", "content": "<?php $t = 'a'; ?>\n<img src=\"photo.png\">\n"
                                                    "<img src=\"<?= $pic ?>\">\n<a href=\"list.php?id=3\">Список</a>"}],
        "listings": [], "shots": []})
    assert missing_images(php) == ["photo.png"] and missing_pages(php) == ["list.php"]
    py = ProjectPlan.model_validate({
        "language": "python", "kind": "console", "name": "x", "entry": "main.py",
        "files": [{"path": "main.py", "content": "print('<img src=\"a.png\">')\n"}], "listings": [], "shots": []})
    assert missing_images(py) == [] and missing_pages(py) == []
