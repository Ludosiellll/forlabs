"""Картинку перетащили из браузера (Telegram Web, сайт): в окно приходит не файл, а ссылка на неё. ForLabs сам
скачивает картинку по ссылке — только из интернета, только картинку и не больше разумного размера."""

import io

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from forlabs.core.llm import FakeLLM
from forlabs.core.settings import Settings
from forlabs.pipeline import weblink
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext


def png(size=(40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 120, 90)).save(buf, format="PNG")
    return buf.getvalue()


def http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_image_by_link_is_downloaded():
    data = png()
    got, mime = weblink.fetch_image("https://example.com/shot.png",
                                    http(lambda req: httpx.Response(200, content=data, headers={"content-type": "image/png"})))
    assert got == data and mime == "image/png"


def test_kind_is_taken_from_the_bytes_not_from_what_the_site_says():
    data = png()
    got, mime = weblink.fetch_image("https://example.com/file",
                                    http(lambda req: httpx.Response(200, content=data, headers={"content-type": "text/plain"})))
    assert mime == "image/png"


def test_page_instead_of_a_picture_is_refused():
    with pytest.raises(weblink.LinkError, match="не картинка"):
        weblink.fetch_image("https://example.com/page",
                            http(lambda req: httpx.Response(200, content=b"<html><body>no picture here</body></html>")))


def test_too_big_picture_is_refused(monkeypatch):
    monkeypatch.setattr(weblink, "MAX_IMAGE_BYTES", 1000)
    with pytest.raises(weblink.LinkError, match="слишком больш"):
        weblink.fetch_image("https://example.com/big.png", http(lambda req: httpx.Response(200, content=b"x" * 5000)))


@pytest.mark.parametrize("url", ["http://localhost/a.png", "http://127.0.0.1:8765/a.png", "http://192.168.1.5/a.png",
                                 "http://10.0.0.1/a.png", "file:///C:/Windows/win.ini", "blob:https://web.telegram.org/x",
                                 "javascript:alert(1)", "http://router.lan/a.png"])
def test_only_pictures_from_the_internet(url):
    calls = []
    with pytest.raises(weblink.LinkError):
        weblink.fetch_image(url, http(lambda req: calls.append(req) or httpx.Response(200, content=png())))
    assert calls == []  # запрос даже не отправлен


def test_redirect_into_the_local_network_is_refused():
    def handler(req):
        if req.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8765/secret.png"})
        return httpx.Response(200, content=png())

    with pytest.raises(weblink.LinkError):
        weblink.fetch_image("https://example.com/a.png", http(handler))


def test_site_that_refuses_gives_a_clear_reason():
    with pytest.raises(weblink.LinkError, match="Ctrl\\+V"):
        weblink.fetch_image("https://example.com/a.png", http(lambda req: httpx.Response(403)))


def client(tmp_home):
    ctx = AppContext(settings=Settings(mode="local", tour_done=True), llm_override=lambda: FakeLLM([]),
                     prefer_word=False, render_diagrams=False, synchronous=True)
    return TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")


def test_endpoint_returns_the_picture(tmp_home, monkeypatch):
    data = png()
    monkeypatch.setattr(weblink, "fetch_image", lambda url, http=None: (data, "image/png"))
    r = client(tmp_home).post("/fetch-dropped", data={"url": "https://example.com/shot.png"})
    assert r.status_code == 200 and r.content == data and r.headers["content-type"] == "image/png"


def test_endpoint_says_why_it_could_not(tmp_home):
    r = client(tmp_home).post("/fetch-dropped", data={"url": "blob:https://web.telegram.org/123"})
    assert r.status_code == 400 and r.json()["error"]
    r = client(tmp_home).post("/fetch-dropped", data={"url": "http://127.0.0.1:8765/settings"})
    assert r.status_code == 400 and "локальн" in r.json()["error"]
