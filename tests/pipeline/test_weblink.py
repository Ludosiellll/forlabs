"""Ссылка как материал лабы: текст страницы с полями форм и кнопками; все случаи, когда страница не добавляется."""

import httpx
import pytest

from forlabs.pipeline import weblink
from forlabs.pipeline.weblink import LinkError

SHOP = """<!doctype html><html><head><title>Магазин «Зерно» — вход</title><style>.x{color:red}</style>
<script>var secret = "не должно попасть";</script></head><body>
<nav><a href="/catalog">Каталог</a> <a href="#top">Наверх</a></nav>
<h1>Вход в личный кабинет</h1>
<p>Введите почту и пароль. Пароль — не меньше 8 символов.</p>
<form action="/login"><label>Почта <input type="email" name="email" placeholder="you@mail.ru"></label>
<input type="password" name="password" placeholder="Пароль"><input type="hidden" name="csrf" value="123">
<select name="city"><option>Минск</option><option>Гомель</option></select>
<button type="submit">Войти</button></form>
<table><tr><th>Товар</th><th>Цена</th></tr><tr><td>Кофе</td><td>12,50</td></tr></table>
<div hidden>скрытый текст</div>
""" + "<p>Описание магазина для теста длины текста. </p>" * 6 + "</body></html>"


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def page(body, ctype="text/html; charset=utf-8", status=200, url=None):
    def handler(request):
        return httpx.Response(status, content=body.encode() if isinstance(body, str) else body,
                              headers={"content-type": ctype})
    return handler


def test_page_text_keeps_what_test_cases_need():
    name, text = weblink.fetch("https://shop.example/login", http=client(page(SHOP)))
    assert name == "Магазин «Зерно» — вход — shop.example"
    assert "# Вход в личный кабинет" in text and "не меньше 8 символов" in text
    assert "[поле email: you@mail.ru]" in text and "[поле password: Пароль]" in text
    assert "[список city: Минск, Гомель]" in text and "[кнопка: Войти]" in text
    assert "Каталог (https://shop.example/catalog)" in text and "| Кофе | 12,50" in text
    assert "secret" not in text and "csrf" not in text and "скрытый текст" not in text and "color:red" not in text


def test_address_without_scheme_gets_https():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return page(SHOP)(request)

    weblink.fetch("shop.example/login", http=client(handler))
    assert seen["url"] == "https://shop.example/login"


@pytest.mark.parametrize("url", ["ftp://x.ru/a", "file:///C:/Users/x/a.txt", "http://localhost:8080",
                                 "http://127.0.0.1/admin", "http://192.168.1.1/", "http://10.0.0.5", "http://[::1]/",
                                 "http://router.local/", "javascript:alert(1)", ""])
def test_local_and_strange_addresses_are_refused(url):
    with pytest.raises(LinkError):
        weblink.fetch(url, http=client(page(SHOP)))


def test_redirect_to_local_address_is_refused():
    def handler(request):
        if request.url.host == "short.example":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        return page(SHOP)(request)

    with pytest.raises(LinkError, match="локальную сеть"):
        weblink.fetch("https://short.example/x", http=client(handler))


@pytest.mark.parametrize("status, words", [(403, "сохрани как PDF"), (429, "сохрани как PDF"), (404, "нет"),
                                           (500, "позже")])
def test_site_errors_are_explained(status, words):
    with pytest.raises(LinkError, match=words):
        weblink.fetch("https://site.example/", http=client(page("x", status=status)))


def test_no_internet_and_timeout_are_explained():
    def offline(request):
        raise httpx.ConnectError("нет сети")

    def slow(request):
        raise httpx.ReadTimeout("долго")

    with pytest.raises(LinkError, match="интернет"):
        weblink.fetch("https://site.example/", http=client(offline))
    with pytest.raises(LinkError, match="25 секунд"):
        weblink.fetch("https://site.example/", http=client(slow))


def test_page_built_by_scripts_asks_for_pdf():
    spa = "<html><head><title>App</title></head><body><div id=root></div><script>render()</script></body></html>"
    with pytest.raises(LinkError, match="скриптами"):
        weblink.fetch("https://app.example/", http=client(page(spa)))


def test_huge_page_is_refused():
    with pytest.raises(LinkError, match="4 МБ"):
        weblink.fetch("https://big.example/", http=client(page("a" * (5 * 1024 * 1024))))


def test_binary_file_link_is_refused_but_pdf_is_read(tmp_path):
    with pytest.raises(LinkError, match="файл"):
        weblink.fetch("https://x.example/setup.exe", http=client(page(b"MZ\x00", ctype="application/octet-stream")))
    import pymupdf as fitz

    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((72, 72), "Variant 7: test the login form " * 3)
    pdf = doc.tobytes()
    name, text = weblink.fetch("https://x.example/files/lab7.pdf", http=client(page(pdf, ctype="application/pdf")))
    assert "lab7.pdf" in name and "Variant 7" in text


def test_link_goes_into_lab_materials_with_limits(tmp_home, tmp_path):
    from test_project_stage import make

    pipe, fake, st = make(tmp_home, tmp_path, [])
    st, problems = pipe.add_link(st, "https://shop.example/login", http=client(page(SHOP)))
    assert problems == [] and st.materials[-1].name.endswith("shop.example")
    assert st.materials[-1].text.startswith("Страница: https://shop.example/login")
    huge = "<html><body>" + "<p>" + "слово " * 9000 + "</p>" * 3 + "</body></html>"
    st, problems = pipe.add_link(st, "https://long.example/", http=client(page(huge)))
    assert problems and "не добавлен" in problems[0]
    st, problems = pipe.add_link(st, "http://localhost/", http=client(page(SHOP)))
    assert problems[0].startswith("Ссылка не добавлена")
