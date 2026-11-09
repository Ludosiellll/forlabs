"""Установка программ для лаб в папку ForLabs: загрузка с докачкой, проверка суммы, безопасная распаковка, пробный
запуск, перенос на место одним шагом — и сбои: нет интернета, битый файл, нет места, антивирус, двойной клик,
ForLabs закрыли посреди установки."""

import errno
import hashlib
import io
import random
import threading
import time
import zipfile
from pathlib import Path

import pytest
from toolfakes import GO_INDEX, Runs, go_site, make_zip

from forlabs.runner import tools
from forlabs.runner.tools import STATUS_DLL_NOT_FOUND, ToolError, safe_extract

GO_ZIP = "https://go.dev/dl/go1.99.1.windows-amd64.zip"
NOISE = random.Random(7).randbytes(20000)  # не сжимается — архив больше мест обрыва


def no_languages(tools_dir):
    raise ImportError("languages.py ещё нет")


def leftovers(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.name.startswith((".st-", ".old-"))) if root.exists() else []


def test_go_is_downloaded_checked_unpacked_and_tried(web, make_mgr):
    go_site(web)
    m = make_mgr(web)
    job = m.install("go")
    assert job.state == "done", job.error
    exe = m.root / "go" / "bin" / "go.exe"
    assert exe.read_bytes() == b"MZ go" and (m.root / "go" / "VERSION").exists()
    tried = m.run.calls[-1]  # пробный запуск — ещё в черновой папке, до переноса на место
    tried_exe = Path(tried[0]).relative_to(m.root)
    assert tried[1:] == ["version"] and tried_exe.parts[0].startswith(".st-go-")
    assert tried_exe.parts[1:] == ("bin", "go.exe")
    s = m.tool_status("go")
    assert s["state"] == "done" and s["percent"] == 100 and s["installed_version"].startswith("go версия")
    assert leftovers(m.root) == [] and list((m.root / ".downloads").iterdir()) == []  # архив удалён


def test_checksum_mismatch_deletes_file_and_says_so(web, make_mgr):
    go_site(web, sha="0" * 64)
    m = make_mgr(web)
    job = m.install("go")
    assert job.state == "error" and "Файл повреждён" in job.error and "Повторить" in job.error
    assert not (m.root / "go").exists() and list((m.root / ".downloads").iterdir()) == []


@pytest.mark.parametrize("evil", ["go/../../escape.txt", "/etc/escape.txt", "C:/escape.txt", "go/bin/con",
                                  "go/x:stream"])
def test_zip_slip_is_rejected_before_anything_is_written(web, make_mgr, tmp_path, evil):
    go_site(web, files={"go/bin/go.exe": b"MZ", evil: b"oops"})
    m = make_mgr(web)
    job = m.install("go")
    assert job.state == "error" and "В архиве опасные пути" in job.error
    assert not (m.root / "go").exists() and leftovers(m.root) == []
    assert not list(tmp_path.rglob("escape.txt"))


def test_symlinks_and_zip_bombs_are_rejected(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        link = zipfile.ZipInfo("go/bin/link")
        link.external_attr = 0o120777 << 16
        zf.writestr(link, "/etc")
    with pytest.raises(ToolError, match="ссылки"):
        safe_extract(zipfile.ZipFile(io.BytesIO(buf.getvalue())), tmp_path / "a")
    bomb = make_zip({"big.bin": b"\0" * (60 * 2**20)})  # 60 МБ нулей сжимаются в десятки КБ
    with pytest.raises(ToolError, match="сжат"):
        safe_extract(zipfile.ZipFile(io.BytesIO(bomb)), tmp_path / "b")
    with pytest.raises(ToolError, match="больше"):
        safe_extract(zipfile.ZipFile(io.BytesIO(make_zip({"a": b"x" * 5000}))), tmp_path / "c", limit=1000)
    assert not (tmp_path / "b").exists() or not list((tmp_path / "b").iterdir())


def test_connection_drop_resumes_from_same_place(web, make_mgr):
    data = go_site(web, files={"go/bin/go.exe": NOISE})
    web.drops[GO_ZIP] = [3000, 4000]
    m = make_mgr(web)
    assert m.install("go").state == "done"
    assert web.downloads(".zip") == [None, "bytes=3000-", "bytes=7000-"]
    assert (m.root / "go" / "bin" / "go.exe").read_bytes() == NOISE and len(data) > 7000


def test_closed_mid_download_then_resumes_after_restart(web, make_mgr):
    go_site(web, files={"go/bin/go.exe": NOISE})
    web.drops[GO_ZIP] = [5000]
    orig = web.handler

    def drop_then_offline(req):  # после обрыва сети нет совсем — повторы кончились
        if web.downloads(".zip"):
            web.offline.add("*")
        return orig(req)

    web.handler = drop_then_offline
    first = make_mgr(web)
    job = first.install("go")
    assert job.state == "error" and "Проверь интернет" in job.error and "с того же места" in job.error
    part = next((first.root / ".downloads").glob("*.part"))
    assert part.stat().st_size == 5000 and not (first.root / "go").exists()
    web.handler, web.calls = orig, []
    web.offline.clear()
    again = make_mgr(web)  # ForLabs открыли снова
    assert again.install("go").state == "done"
    assert web.downloads(".zip") == ["bytes=5000-"]


def test_app_closed_mid_install_leaves_no_half_installed_tool(web, make_mgr, monkeypatch):
    go_site(web)
    m = make_mgr(web)
    real = tools.safe_extract

    def dies_halfway(zf, dest, prefix="", limit=tools.UNPACK_LIMIT):
        (dest / "bin").mkdir(parents=True)
        (dest / "bin" / "go.exe").write_bytes(b"MZ half")
        raise KeyboardInterrupt  # процесс убит посреди распаковки

    monkeypatch.setattr(tools, "safe_extract", dies_halfway)
    with pytest.raises(KeyboardInterrupt):
        m._install("go")
    assert not (m.root / "go").exists()  # на месте программы — ничего, а не половина
    (m.root / ".st-go-dead00" / "bin").mkdir(parents=True)  # черновик, оставшийся от убитого процесса
    monkeypatch.setattr(tools, "safe_extract", real)
    again = make_mgr(web)
    assert again.install("go").state == "done" and leftovers(again.root) == []
    assert (again.root / "go" / "bin" / "go.exe").read_bytes() == b"MZ go"


def test_tool_already_in_place_is_only_checked(web, make_mgr):
    m = make_mgr(web, detect=no_languages)
    exe = m.root / "go" / "bin" / "go.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    assert m.install("go").state == "done" and web.calls == []  # ничего не качал
    assert m.found()["go"]["path"] == str(exe)  # без languages.detect — видно хотя бы своё


def test_broken_copy_is_replaced(web, make_mgr):
    go_site(web)
    m = make_mgr(web)
    exe = m.root / "go" / "bin" / "go.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"old")
    (m.root / "go" / "junk.txt").write_text("старое")
    runs = m.run
    runs.codes["go.exe"] = (1, "сломано")

    def heal(cmd, timeout=None, cwd=None):  # новая копия запускается
        if ".st-go" in str(cmd[0]):
            runs.codes.pop("go.exe", None)
        return runs(cmd, timeout, cwd)

    m.run = heal
    assert m.install("go").state == "done"
    assert exe.read_bytes() == b"MZ go" and not (m.root / "go" / "junk.txt").exists() and leftovers(m.root) == []


def test_double_click_installs_once(web, make_mgr):
    go_site(web)
    web.gate = threading.Event()
    m = make_mgr(web)
    assert m.start_install("go") and m.start_install("go")
    assert m.tool_status("go")["state"] == "downloading"
    web.gate.set()
    for _ in range(500):
        if m.tool_status("go")["state"] == "done":
            break
        time.sleep(0.01)
    assert m.tool_status("go")["state"] == "done" and len(web.downloads(".zip")) == 1


def test_no_internet(web, make_mgr):
    go_site(web)
    web.offline.add("*")
    job = make_mgr(web).install("go")
    assert job.state == "error" and "Нет связи с go.dev" in job.error


def test_disk_full_before_and_during_download(web, make_mgr):
    go_site(web)
    m = make_mgr(web)
    m.free_space = lambda p: 100 * 2**20
    job = m.install("go")
    assert job.state == "error" and "мало места" in job.error and "МБ" in job.error and web.calls[-1][0] == GO_INDEX

    class Full:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def write(self, b):
            raise OSError(errno.ENOSPC, "No space left on device")

    m = make_mgr(web)
    m.opener = Full
    job = m.install("go")
    assert job.state == "error" and "мало места" in job.error


def test_antivirus_deletes_download_or_unpacked_program(web, make_mgr, monkeypatch):
    go_site(web)
    m = make_mgr(web)

    class Quarantine:  # антивирус удаляет файл, как только загрузка его закрыла
        def __init__(self, path, mode):
            self.path, self.f = Path(path), open(path, mode)

        def __enter__(self):
            return self.f

        def __exit__(self, *a):
            self.f.close()
            self.path.unlink()

    m.opener = Quarantine
    job = m.install("go")
    assert job.state == "error" and "антивирус" in job.error
    real = tools.safe_extract

    def extract_then_quarantine(zf, dest, prefix="", limit=tools.UNPACK_LIMIT):
        real(zf, dest, prefix, limit)
        (dest / "bin" / "go.exe").unlink()

    monkeypatch.setattr(tools, "safe_extract", extract_then_quarantine)
    m = make_mgr(web)
    job = m.install("go")
    assert job.state == "error" and "антивирус" in job.error and not (m.root / "go").exists()


def test_damaged_archive_is_explained_and_downloaded_again(web, make_mgr):
    junk = b"PK\x03\x04" + b"\x00" * 300  # начало похоже на zip, дальше мусор — а сумма сходится
    web.json(GO_INDEX, [{"version": "go1.99.1", "stable": True, "files": [
        {"filename": "go1.99.1.windows-amd64.zip", "os": "windows", "arch": "amd64", "kind": "archive",
         "sha256": hashlib.sha256(junk).hexdigest(), "size": len(junk)}]}])
    web.add(GO_ZIP, junk)
    m = make_mgr(web)
    job = m.install("go")
    assert job.state == "error" and "Архив повреждён" in job.error
    assert list((m.root / ".downloads").iterdir()) == [] and leftovers(m.root) == []


def test_not_the_expected_program_in_archive(web, make_mgr):
    go_site(web, files={"readme.txt": b"hello"})
    job = make_mgr(web).install("go")
    assert job.state == "error" and "В архиве нет go.exe — это не Go" in job.error


def php_site(web, files):
    data = make_zip(files)
    index = "https://windows.php.net/downloads/releases/releases.json"
    web.json(index, {"8.5": {"version": "8.5.1", "nts-vs17-x64": {"zip": {
        "path": "php-8.5.1-nts-Win32-vs17-x64.zip", "sha256": hashlib.sha256(data).hexdigest()}}}})
    web.add("https://windows.php.net/downloads/releases/php-8.5.1-nts-Win32-vs17-x64.zip", data)


def test_missing_visual_cpp_is_explained_and_download_kept(web, make_mgr):
    php_site(web, {"php.exe": b"MZ", "php.ini-development": b'; ini\n;extension_dir = "ext"\n;extension=mysqli\n'})
    m = make_mgr(web)
    m.run.codes["php.exe"] = (STATUS_DLL_NOT_FOUND, "")
    job = m.install("php")
    assert job.state == "error" and "Visual C++" in job.error and job.help == "vcredist"
    assert not (m.root / "php").exists() and m.tool_status("php")["help"] == "vcredist"
    m.run.codes.pop("php.exe")  # студент поставил Visual C++ и нажал «Повторить»
    assert m.install("php").state == "done"
    assert len(web.downloads(".zip")) == 1  # проверенный архив не качался второй раз
    ini = (m.root / "php" / "php.ini").read_text()
    assert f'extension_dir = "{(m.root / "php" / "ext").as_posix()}"' in ini and "\nextension=mysqli" in ini


def mysql_site(web):
    data = make_zip({"mysql-8.4.7-winx64/bin/mysql.exe": b"MZ", "mysql-8.4.7-winx64/bin/mysqld.exe": b"MZ",
                     "mysql-8.4.7-winx64/bin/mysqladmin.exe": b"MZ", "mysql-8.4.7-winx64/share/english/x": b"1"})
    page = "https://dev.mysql.com/downloads/mysql/8.4.html?os=3"
    web.add(page, f"<td>(mysql-8.4.7-winx64.zip)</td><td>MD5: <code class=\"md5\">{hashlib.md5(data).hexdigest()}"
                  "</code></td>")
    web.add("https://dev.mysql.com/get/Downloads/MySQL-8.4/mysql-8.4.7-winx64.zip", data)


def test_mysql_gets_a_private_empty_database(web, make_mgr):
    mysql_site(web)
    m = make_mgr(web)
    assert m.install("mysql").state == "done"
    base = m.root / "mysql"
    assert (base / "bin" / "mysql.exe").exists() and (base / "bin" / "mysqld.exe").exists()
    assert (base / "data" / "ibdata1").exists()  # база готовилась в черновой папке и переехала вместе с программой
    init = next(c for c in m.run.calls if "--initialize-insecure" in c)
    assert init[1] == "--no-defaults" and Path(init[0]).relative_to(m.root).parts[0].startswith(".st-mysql-")


def test_mysql_init_failure_leaves_nothing(web, make_mgr):
    mysql_site(web)
    runs = Runs()
    runs.init_code = 1
    m = make_mgr(web, run=runs)
    job = m.install("mysql")
    assert job.state == "error" and "MySQL не подготовил базу" in job.error
    assert not (m.root / "mysql").exists() and leftovers(m.root) == []


def test_python_package_layout_without_extras(web, make_mgr):
    data = make_zip({"python.nuspec": b"<x/>", "tools/python.exe": b"MZ", "tools/Lib/os.py": b"#",
                     "tools/Lib/venv/scripts/nt/python.exe": b"MZ venv"})
    web.offline.add("www.python.org")
    import base64

    reg = "https://api.nuget.org/v3/registration5-gz-semver2/python/index.json"
    cat = "https://api.nuget.org/v3/catalog0/data/x/python.3.15.2.json"
    pkg = "https://api.nuget.org/v3-flatcontainer/python/3.15.2/python.3.15.2.nupkg"
    web.json(reg, {"items": [{"@id": "p", "items": [{"packageContent": pkg, "catalogEntry": {
        "@id": cat, "version": "3.15.2", "listed": True, "packageContent": pkg}}]}]})
    web.json(cat, {"packageHash": base64.b64encode(hashlib.sha512(data).digest()).decode(),
                   "packageHashAlgorithm": "SHA512", "packageSize": len(data)})
    web.add(pkg, data)
    m = make_mgr(web)
    assert m.install("python").state == "done"
    assert (m.root / "python" / "python.exe").read_bytes() == b"MZ" and (m.root / "python" / "Lib" / "os.py").exists()
    assert not (m.root / "python" / "python.nuspec").exists() and not (m.root / "python" / "tools").exists()


def test_manual_tools_are_not_installed(web, make_mgr):
    m = make_mgr(web)
    for key in ("msvc", "clang", "edge"):
        with pytest.raises(ToolError, match="открой сайт"):
            m.install(key)
    assert web.calls == []


def test_status_uses_languages_detect_and_refreshes_after_install(web, make_mgr):
    go_site(web)
    seen = []

    class TC:
        def __init__(self, path, version):
            self.name, self.path, self.version = "x", path, version

    def detect(tools_dir):
        seen.append(tools_dir)
        exe = tools_dir / "go" / "bin" / "go.exe"
        found = {"edge": TC(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "131.0")}
        if exe.exists():
            found["go"] = TC(exe, "go1.99.1")
        return found

    m = make_mgr(web, detect=detect)
    s = m.status()["tools"]
    assert s["edge"]["found"] and s["edge"]["version"] == "131.0" and not s["go"]["found"]
    m.status()
    assert len(seen) == 1  # опрос страницы не ищет каждый раз заново
    m.install("go")
    s = m.status()["tools"]["go"]
    assert s["found"] and s["version"] == "go1.99.1" and len(seen) == 2 and seen[0] == m.root
