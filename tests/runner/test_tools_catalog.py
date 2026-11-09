"""Каталог программ для лаб: раскладка в папке tools и разбор ответа каждого издателя (образцы — tests/fixtures/tools;
.NET, Node.js и NuGet — урезанные настоящие ответы, остальные — по описанию формата у издателя)."""

import base64
import json

import pytest
from toolfakes import GO_INDEX, Web, fixture

from forlabs.runner import tools
from forlabs.runner.tools import ToolError, catalog

DOTNET_INDEX = "https://builds.dotnet.microsoft.com/dotnet/release-metadata/releases-index.json"
DOTNET_8 = "https://builds.dotnet.microsoft.com/dotnet/release-metadata/8.0/releases.json"
PY_INDEX = "https://www.python.org/ftp/python/index-windows.json"
NUGET_REG = "https://api.nuget.org/v3/registration5-gz-semver2/python/index.json"
NUGET_PAGE = "https://api.nuget.org/v3/registration5-gz-semver2/python/page/3.15.0/3.16.0-a1.json"
NUGET_CAT = "https://api.nuget.org/v3/catalog0/data/2026.10.26.04.37.04/python.3.15.2.json"
NODE_INDEX = "https://nodejs.org/dist/index.json"
ADOPTIUM = ("https://api.adoptium.net/v3/assets/latest/21/hotspot?architecture=x64&image_type=jdk&os=windows"
            "&vendor=eclipse")
PHP_INDEX = "https://windows.php.net/downloads/releases/releases.json"
WINLIBS_API = "https://api.github.com/repos/brechtsanders/winlibs_mingw/releases?per_page=20"
MYSQL_PAGE = "https://dev.mysql.com/downloads/mysql/8.4.html?os=3"


def test_every_tool_key_is_in_catalog_with_the_layout_languages_detect_expects():
    c = catalog()
    assert set(c) == {"dotnet", "msvc", "gpp", "clang", "python", "java", "node", "go", "php", "mysql", "edge"}
    layout = {k: f"{t.folder}/{t.exe}" for k, t in c.items() if t.portable}
    assert layout == {"dotnet": "dotnet/dotnet.exe", "python": "python/python.exe", "node": "node/node.exe",
                      "go": "go/bin/go.exe", "java": "jdk/bin/javac.exe", "php": "php/php.exe",
                      "gpp": "mingw64/bin/g++.exe", "mysql": "mysql/bin/mysql.exe"}
    assert c["mysql"].also == ("bin/mysqld.exe",)
    for t in c.values():
        assert t.name and t.purpose and t.size_mb > 0 and t.page.startswith("https://"), t.key
        for src in t.sources:
            assert src["type"] in tools.RESOLVERS
            assert all(v.startswith("https://") for v in src.values() if str(v).startswith(("http:", "https:")))
    # MSVC ставится только с сайта Microsoft — предлагаем страницу и g++; Edge — часть Windows
    assert c["msvc"].kind == "manual" and c["msvc"].alternative == "gpp"
    assert "visualstudio.microsoft.com" in c["msvc"].page
    assert c["clang"].alternative == "gpp"
    assert c["edge"].kind == "system" and "microsoft.com" in c["edge"].page
    assert tools.vcredist()["url"].startswith("https://aka.ms/")


def resolve(web: Web, key: str, make_mgr):
    return make_mgr(web).resolve(key)


def test_dotnet_sdk_8_zip_and_sha512_from_release_metadata(web, make_mgr):
    web.add(DOTNET_INDEX, fixture("dotnet-releases-index.json"))
    web.add(DOTNET_8, fixture("dotnet-8.0-releases.json"))
    a = resolve(web, "dotnet", make_mgr)
    assert a.url == "https://builds.dotnet.microsoft.com/dotnet/Sdk/8.0.427/dotnet-sdk-8.0.427-win-x64.zip"
    assert a.version == "8.0.427" and a.hash_algo == "sha512"
    assert a.hash == ("a4dd6eb2bf2a2f6fb10997d3e044a229b23ad96a5c86d20494687e34ee73b537d7bd14488ce14cded6917f6778da4d"
                      "66360e2734020b7ef487d0f29ab144489d")


def test_python_from_python_org_index_newest_stable_x64(web, make_mgr):
    web.add(PY_INDEX, fixture("python-index-windows.json"))
    a = resolve(web, "python", make_mgr)  # не альфа 3.16, не free-threaded, не ARM64
    assert a.url == "https://www.python.org/ftp/python/3.15.2/python-3.15.2-amd64.zip"  # относительный адрес
    assert a.version == "3.15.2" and a.hash_algo == "sha256" and a.hash == "4" * 64


def test_python_falls_back_to_nuget_package_with_its_sha512(web, make_mgr):
    web.offline.add("www.python.org")
    web.add(NUGET_REG, fixture("nuget-registration.json"))
    web.add(NUGET_PAGE, fixture("nuget-page.json"))
    web.add(NUGET_CAT, fixture("nuget-catalog-3.15.2.json"))
    a = resolve(web, "python", make_mgr)  # 3.16.0-a1 — предварительная, пропущена
    cat = json.loads(fixture("nuget-catalog-3.15.2.json"))
    assert a.url == "https://api.nuget.org/v3-flatcontainer/python/3.15.2/python.3.15.2.nupkg"
    assert a.version == "3.15.2" and a.hash_algo == "sha512" and a.size == 16672834
    assert a.hash == base64.b64decode(cat["packageHash"]).hex()


def test_node_latest_lts_zip_with_sum_from_shasums(web, make_mgr):
    web.add(NODE_INDEX, fixture("node-index.json"))
    web.add("https://nodejs.org/dist/v26.13.0/SHASUMS256.txt", fixture("node-SHASUMS256.txt"))
    a = resolve(web, "node", make_mgr)  # не v27 alpha
    assert a.url == "https://nodejs.org/dist/v26.13.0/node-v26.13.0-win-x64.zip" and a.version == "v26.13.0"
    assert a.hash == "dcc60d59a0746999f99064a01c020445d54d84228c691cdbf25b49ac3b59150a"  # не darwin-x64


def test_go_stable_windows_amd64_zip(web, make_mgr):
    web.add(GO_INDEX, fixture("go-dl.json"))
    a = resolve(web, "go", make_mgr)
    assert a.url == "https://go.dev/dl/go1.25.4.windows-amd64.zip" and a.version == "go1.25.4"
    assert a.hash == "dddd" + "0" * 60 and a.size == 78000000  # zip, а не msi и не 386


def test_temurin_21_zip_not_msi(web, make_mgr):
    web.add(ADOPTIUM, fixture("adoptium-21.json"))
    a = resolve(web, "java", make_mgr)
    assert a.url.endswith("/OpenJDK21U-jdk_x64_windows_hotspot_21.0.9_10.zip") and a.version == "jdk-21.0.9+10"
    assert a.hash == "abcd" + "0" * 60 and a.size == 191000000 and a.filename.endswith("_21.0.9_10.zip")


def test_php_newest_series_nts_x64_next_to_moved_releases_json(web, make_mgr):
    moved = "https://downloads.php.net/~windows/releases/releases.json"
    web.redirect(PHP_INDEX, moved)
    web.add(moved, fixture("php-releases.json"))
    a = resolve(web, "php", make_mgr)
    assert a.url == "https://downloads.php.net/~windows/releases/php-8.5.1-nts-Win32-vs17-x64.zip"
    assert a.version == "8.5.1" and a.hash == "85" + "0" * 62


def test_winlibs_release_zip_and_sum_from_its_sha256_file(web, make_mgr):
    rels = json.loads(fixture("github-winlibs.json"))
    web.add(WINLIBS_API, json.dumps(rels))
    side = rels[1]["assets"][3]["browser_download_url"]
    web.add(side, "ab" * 32 + "  winlibs-x86_64-posix-seh-gcc-15.2.0-mingw-w64ucrt-13.0.0-r3.zip\n")
    a = resolve(web, "gpp", make_mgr)  # не предварительный 16.1, не i686, не сборка с LLVM
    assert a.url.endswith("/winlibs-x86_64-posix-seh-gcc-15.2.0-mingw-w64ucrt-13.0.0-r3.zip")
    assert a.version == "15.2.0posix-13.0.0-ucrt-r3" and a.hash == "ab" * 32 and a.size == 262000000
    rels[1]["assets"].pop(3)  # файла суммы нет — сумма, которую показывает GitHub
    web.add(WINLIBS_API, json.dumps(rels))
    assert resolve(web, "gpp", make_mgr).hash == "1502" + "0" * 60


def test_mysql_zip_and_md5_from_download_page(web, make_mgr):
    web.add(MYSQL_PAGE, fixture("mysql-downloads.html"))
    a = resolve(web, "mysql", make_mgr)  # не msi и не debug-test
    assert a.url == "https://dev.mysql.com/get/Downloads/MySQL-8.4/mysql-8.4.7-winx64.zip"
    assert a.version == "8.4.7" and a.hash_algo == "md5" and a.hash == "7b3c1f0e9d2a4b5c6d7e8f9012345678"


@pytest.mark.parametrize("answer, words", [
    ({"unexpected": 1}, "сайт мог измениться"),
    ("<html><body>Войдите в сеть Wi-Fi колледжа</body></html>", "Wi-Fi"),
])
def test_changed_site_or_wifi_login_page_is_explained(web, make_mgr, answer, words):
    web.add(GO_INDEX, answer if isinstance(answer, str) else json.dumps(answer))
    with pytest.raises(ToolError, match=words):
        resolve(web, "go", make_mgr)


def test_no_internet_and_server_errors_are_explained(web, make_mgr):
    web.offline.add("*")
    with pytest.raises(ToolError, match="нет связи с go.dev.*интернет"):
        resolve(web, "go", make_mgr)
    web.offline.clear()
    web.add(GO_INDEX, "busy", status=503)
    with pytest.raises(ToolError, match="ответ 503"):
        resolve(web, "go", make_mgr)
    web.add(WINLIBS_API, "{}", status=403, headers={"x-ratelimit-remaining": "0"})
    with pytest.raises(ToolError, match="GitHub временно ограничил"):
        resolve(web, "gpp", make_mgr)


def test_redirect_to_plain_http_is_refused(web, make_mgr):
    web.redirect(GO_INDEX, "http://go.dev/dl/?mode=json")
    web.add("http://go.dev/dl/?mode=json", fixture("go-dl.json"))
    with pytest.raises(ToolError, match="незащищённый"):
        resolve(web, "go", make_mgr)
