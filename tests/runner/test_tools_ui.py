"""Окно: «Программы для лаб» в Настройках, маршруты /tools/…, блок «нет программы» для страницы лабы.
Студент (не администратор) тоже ставит программы — это его компьютер."""

import sys
from pathlib import Path

from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader
from toolfakes import go_site, make_zip

from forlabs.core.settings import Settings
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

sys.path.insert(0, str(Path(__file__).parents[1] / "ui"))
from test_admin import fake, make_client  # noqa: E402, F401

TEMPLATES = Path(__file__).parents[2] / "forlabs" / "ui" / "templates"


class TC:
    def __init__(self, path, version):
        self.name, self.path, self.version = "x", path, version


def local_client(mgr):
    ctx = AppContext(settings=Settings(mode="local", tour_done=True), prefer_word=False, synchronous=True)
    ctx.tools = mgr
    return TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")


def test_settings_lists_tools_found_and_missing(web, make_mgr, tmp_home):
    mgr = make_mgr(web, detect=lambda tools_dir: {"python": TC(r"C:\Python313\python.exe", "Python 3.13.1")})
    page = local_client(mgr).get("/settings").text
    assert "Программы для лаб" in page and 'data-tools' in page
    for name in (".NET SDK", "g++ (MinGW-w64)", "Python 3", "JDK 21 (Temurin)", "Node.js LTS", "Go", "PHP",
                 "MySQL Server 8.4", "Visual C++ (MSVC)", "Microsoft Edge"):
        assert name in page, name
    assert "Скачать и установить (≈80 МБ)" in page and "У меня есть ссылка" in page
    assert r"Python 3.13.1 · C:\Python313\python.exe" in page  # найденный: версия и путь
    assert 'href="https://visualstudio.microsoft.com/visual-cpp-build-tools/"' in page  # MSVC — только сайт


def test_student_member_can_install_and_use_link(fake, tmp_home, web, make_mgr):  # noqa: F811
    client = make_client(fake, tmp_home, me="member")
    client.app.state.ctx.tools = make_mgr(web)
    assert "Программы для лаб" in client.get("/settings").text
    go_site(web)
    r = client.post("/tools/go/install")
    assert r.status_code == 200 and r.json()["state"] == "done"
    web.add("https://files.example.org/setup.exe", b"MZ" + b"\0" * 100)
    assert client.post("/tools/link", data={"url": "https://files.example.org/setup.exe"}).json()["ok"] is True


def test_status_route_and_install_route(web, make_mgr, tmp_home):
    go_site(web)
    mgr = make_mgr(web)
    client = local_client(mgr)
    s = client.get("/tools/status").json()
    assert set(s["tools"]) >= {"dotnet", "go", "mysql", "edge"} and s["link"]["state"] == "idle"
    assert s["tools"]["go"]["state"] == "idle" and s["tools"]["go"]["size_mb"] == 80
    assert s["vcredist"]["url"].startswith("https://")
    r = client.post("/tools/go/install").json()
    assert r["ok"] and r["state"] == "done" and r["percent"] == 100
    assert client.get("/tools/status").json()["tools"]["go"]["state"] == "done"
    assert client.post("/tools/nope/install").status_code == 404
    r = client.post("/tools/msvc/install")
    assert r.status_code == 409 and "visualstudio.microsoft.com" in r.json()["page"]


def test_link_route_preview_download_open_and_refusals(web, make_mgr, tmp_home):
    mgr = make_mgr(web)
    client = local_client(mgr)
    url = "https://files.example.org/setup.exe"
    web.add(url, b"MZ" + b"\0" * 2000)
    r = client.post("/tools/link", data={"url": url, "key": "php"}).json()
    assert r["ok"] and r["link"]["state"] == "ready" and r["link"]["host"] == "files.example.org"
    assert r["link"]["filename"] == "setup.exe" and r["link"]["key"] == "php" and mgr.opened == []
    r = client.post("/tools/link", data={"url": url, "key": "php", "go": "1"}).json()
    assert r["link"]["state"] == "done" and mgr.opened == [str(mgr.downloads() / "setup.exe")]
    assert client.post("/tools/link/open").json()["ok"] and len(mgr.opened) == 2
    r = client.post("/tools/link", data={"url": "http://files.example.org/setup.exe"})
    assert r.status_code == 400 and "https://" in r.json()["error"]
    r = client.post("/tools/link", data={"url": url}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403  # чужой сайт не заставит ForLabs качать


def test_link_route_unpacks_known_tool_zip(web, make_mgr, tmp_home):
    mgr = make_mgr(web)
    client = local_client(mgr)
    url = "https://mirror.example.org/go1.25.4.windows-amd64.zip"
    web.add(url, make_zip({"go/bin/go.exe": b"MZ", "go/VERSION": b"go1.25.4"}))
    client.post("/tools/link", data={"url": url, "key": "go"})
    r = client.post("/tools/link", data={"url": url, "key": "go", "go": "1"}).json()
    assert r["link"]["unpack"] == "go" and mgr.opened == []
    r = client.post("/tools/link/unpack").json()
    assert r["ok"] and r["key"] == "go" and r["state"] == "done"
    assert (mgr.root / "go" / "bin" / "go.exe").exists()


def test_missing_block_for_lab_page(web, make_mgr, tmp_home):
    go_site(web)
    mgr = make_mgr(web)
    client = local_client(mgr)
    html = client.get("/tools/go/missing").text
    assert 'data-tool-missing="go"' in html and "data-missing" in html and "Для этого рисунка нужен Go" in html
    assert "Скачать и установить (≈80 МБ)" in html and "У меня есть ссылка" in html
    assert "<html" not in html  # кусок страницы, а не страница
    client.post("/tools/go/install")
    assert "Готово — нажми «Повторить»." in client.get("/tools/go/missing").text
    assert client.get("/tools/nope/missing").status_code == 404
    msvc = client.get("/tools/msvc/missing").text  # MSVC не ставится сам — сайт и g++ взамен
    assert "Открыть сайт" in msvc and "Поставить g++ (MinGW-w64) (≈260 МБ)" in msvc
    assert 'data-tool-install="gpp"' in msvc


def test_missing_block_included_in_a_template(web, make_mgr, tmp_home):
    """Так его подключит страница лабы: {% include "_tool_missing.html" %} с переменной tool."""
    mgr = make_mgr(web)
    mgr._set("php", state="error", error="PHP не запускается: в Windows нет «Visual C++».", help="vcredist")
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True)
    html = env.from_string('<section>{% with tool=tools.tool_status("php") %}{% include "_tool_missing.html" %}'
                           "{% endwith %}</section>").render(tools=mgr)
    assert 'data-tool="php"' in html and "Visual C++" in html and "Скачать и установить (≈32 МБ)" in html
