"""«У меня есть ссылка»: сначала сайт, имя и размер; потом загрузка в «Загрузки» и открытие файла (установщик студент
проходит сам). Только https, не больше 2 ГБ; архив известной программы можно распаковать в папку ForLabs."""

import pytest
from toolfakes import make_zip

from forlabs.runner import tools
from forlabs.runner.tools import ToolError

SETUP = "https://files.example.org/get?id=42"
SETUP_BODY = b"MZ" + b"\x90" * 5000


def site(web, url=SETUP, body=SETUP_BODY, headers=None):
    web.add(url, body, headers={"content-disposition": "attachment; filename*=UTF-8''%D0%A1%D1%80%D0%B5%D0%B4%D0%B0-"
                                                       "setup.exe", **(headers or {})})


def test_preview_then_download_to_downloads_and_open(web, make_mgr):
    site(web)
    m = make_mgr(web)
    info = m.check_link(SETUP)
    assert info["state"] == "ready" and info["host"] == "files.example.org" and info["filename"] == "Среда-setup.exe"
    assert info["size_mb"] == round(len(SETUP_BODY) / 2**20, 1) and m.opened == []
    before = len(web.calls)
    m.start_link(SETUP, wait=True)
    s = m.link_status()
    target = m.downloads() / "Среда-setup.exe"
    assert s["state"] == "done" and s["path"] == str(target) and target.read_bytes() == SETUP_BODY
    assert m.opened == [str(target)] and s["opened"] and s["unpack"] == ""  # открыт, а не запущен молча
    assert len(web.calls) == before + 1 and not list(m.downloads().glob("*.part"))


def test_download_waits_for_preview_of_the_same_link(web, make_mgr):
    site(web)
    m = make_mgr(web)
    s = m.start_link(SETUP, wait=True)  # «Скачать» без проверки — сначала покажем сайт и размер
    assert s["state"] == "ready" and m.opened == [] and not m.downloads().exists()


@pytest.mark.parametrize("url", ["http://files.example.org/setup.exe", "ftp://files.example.org/setup.exe",
                                 "file:///C:/Windows/notepad.exe", "javascript:alert(1)", "files.example.org/a.exe",
                                 ""])
def test_only_https_links(web, make_mgr, url):
    with pytest.raises(ToolError, match="https://"):
        make_mgr(web).check_link(url)
    assert web.calls == []


def test_redirect_to_http_is_refused(web, make_mgr):
    web.redirect(SETUP, "http://mirror.example.org/setup.exe")
    web.add("http://mirror.example.org/setup.exe", SETUP_BODY)
    with pytest.raises(ToolError, match="незащищённый"):
        make_mgr(web).check_link(SETUP)


def test_files_over_2_gb_are_refused(web, make_mgr, monkeypatch):
    site(web, headers={"content-length": str(3 * 2**30)})
    with pytest.raises(ToolError, match="больше 2 ГБ"):
        make_mgr(web).check_link(SETUP)
    site(web)  # при проверке размер был в норме, а файл оказался больше предела — загрузка оборвётся
    m = make_mgr(web)
    m.check_link(SETUP)
    monkeypatch.setattr(tools, "LINK_LIMIT", 1000)
    m.start_link(SETUP, wait=True)
    s = m.link_status()
    assert s["state"] == "error" and "2 ГБ" in s["error"] and not list(m.downloads().iterdir())


def test_web_page_instead_of_file(web, make_mgr):
    web.add("https://www.example.org/downloads/", "<html>Скачайте</html>", headers={"content-type": "text/html"})
    with pytest.raises(ToolError, match="страница, а не файл"):
        make_mgr(web).check_link("https://www.example.org/downloads/")


def test_zip_of_known_tool_is_offered_for_unpacking_instead(web, make_mgr):
    url = "https://mirror.example.org/node-v26.13.0-win-x64.zip"
    web.add(url, make_zip({"node-v26.13.0-win-x64/node.exe": b"MZ node", "node-v26.13.0-win-x64/npm.cmd": b"@"}))
    m = make_mgr(web)
    m.check_link(url, key="python")  # студент нажал в строке Python, а дал Node.js — смотрим по содержимому
    m.start_link(url, wait=True)
    s = m.link_status()
    assert s["state"] == "done" and s["unpack"] == "node" and s["unpack_name"] == "Node.js LTS"
    assert m.opened == []  # архив не открывается сам — предлагаем распаковать
    assert m.install_from_link(wait=True) is True
    assert m.tool_status("node")["state"] == "done"
    assert (m.root / "node" / "node.exe").read_bytes() == b"MZ node"
    assert (m.downloads() / "node-v26.13.0-win-x64.zip").exists()  # файл студента не тронут


def test_broken_zip_from_link_is_not_deleted(web, make_mgr):
    url = "https://mirror.example.org/node-v26.13.0-win-x64.zip"
    good = make_zip({"node-v26.13.0-win-x64/node.exe": b"MZ node"})
    web.add(url, good)
    m = make_mgr(web)
    m.check_link(url)
    m.start_link(url, wait=True)
    path = m.downloads() / "node-v26.13.0-win-x64.zip"
    path.write_bytes(good[:-30])  # файл испортился до «Распаковать»
    m.install_from_link(wait=True)
    s = m.tool_status("node")
    assert s["state"] == "error" and "Архив повреждён" in s["error"] and path.exists()


def test_other_zip_is_just_opened(web, make_mgr):
    url = "https://files.example.org/lab-materials.zip"
    web.add(url, make_zip({"readme.txt": b"hi"}))
    m = make_mgr(web)
    m.check_link(url)
    m.start_link(url, wait=True)
    assert m.link_status()["unpack"] == "" and m.opened == [str(m.downloads() / "lab-materials.zip")]
    assert m.install_from_link(wait=True) is False


def test_existing_file_is_not_overwritten(web, make_mgr):
    site(web)
    m = make_mgr(web)
    m.downloads().mkdir(parents=True)
    (m.downloads() / "Среда-setup.exe").write_bytes(b"old")
    m.check_link(SETUP)
    m.start_link(SETUP, wait=True)
    assert (m.downloads() / "Среда-setup.exe").read_bytes() == b"old"
    assert (m.downloads() / "Среда-setup (1).exe").read_bytes() == SETUP_BODY


def test_link_download_resumes_after_drop(web, make_mgr):
    site(web)
    m = make_mgr(web)
    m.check_link(SETUP)
    web.drops[SETUP] = [1000]
    m.start_link(SETUP, wait=True)
    assert m.link_status()["state"] == "done"
    assert [r for u, r in web.calls if u == SETUP][-2:] == [None, "bytes=1000-"]
    assert (m.downloads() / "Среда-setup.exe").read_bytes() == SETUP_BODY


def test_wifi_login_page_instead_of_file(web, make_mgr):
    site(web)
    m = make_mgr(web)
    m.check_link(SETUP)
    web.routes[SETUP] = (200, b"<!DOCTYPE html><html>Wi-Fi</html>", {})
    m.start_link(SETUP, wait=True)
    s = m.link_status()
    assert s["state"] == "error" and "Wi-Fi" in s["error"] and m.opened == [] and not list(m.downloads().iterdir())


def test_file_name_from_link_is_made_safe():
    assert tools._file_name('attachment; filename="..\\..\\evil.exe"', "https://x.org/a") == "evil.exe"
    assert tools._file_name("", "https://x.org/dl/Python%203.zip?x=1") == "Python 3.zip"
    assert tools._file_name("", "https://x.org/") == "файл"
    assert tools._file_name('attachment; filename="CON"', "https://x.org/a") == "_CON"
