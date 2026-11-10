"""Программы для лаб на компьютере студента: установка без прав администратора и загрузка по ссылке.

Каталог — tools_catalog.json рядом (адреса и размеры правятся там). Программа ставится в папку ForLabs
`data_home()/tools/<folder>`, ровно в том виде, в каком её ищет languages.detect (tools/go/bin/go.exe и т.д.):

1. последняя подходящая версия и её контрольная сумма — у издателя (_resolve_<type>);
2. загрузка в tools/.downloads/*.part с докачкой и повторами (core/download.py, как у обновления ForLabs);
3. проверка суммы; распаковка в tools/.st-<key>-… с защитой от путей за пределы папки и от «zip-бомб»;
4. пробный запуск (`go version`), доводка (php.ini, пустая база MySQL) — ещё в черновой папке;
5. переименование черновой папки в tools/<folder> одним шагом. ForLabs закрыли посреди — в tools/<folder> нет
   полуустановленной программы: черновые папки удаляются при следующей установке, недокачанный файл докачивается.

Своя ссылка («У меня есть ссылка»): файл с https-адреса качается в «Загрузки» и открывается — установщик студент
проходит сам, ForLabs ничего не запускает молча. Архив известной программы можно вместо этого распаковать в tools.

MySQL для снимков: при установке mysqld готовит пустую базу в tools/mysql/data (`--initialize-insecure`) — так
видно, что сервер работает. Снимки идут на отдельном сервере с чистой папкой данных на каждую лабу
(runner/mysqlrun.py, этап «Проект»).
"""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import unquote, urljoin, urlparse

from forlabs.core import download
from forlabs.core.download import RETRY_PAUSES, Retry, Stop

log = logging.getLogger("forlabs")
CATALOG_FILE = Path(__file__).with_name("tools_catalog.json")
MB = 2**20
LINK_LIMIT = 2 * 2**30  # своя ссылка: больше 2 ГБ не качаем
TOOL_LIMIT = 2 * 2**30
UNPACK_LIMIT = 6 * 2**30  # распакованный архив программы
MAX_ENTRIES = 300_000
VERSION_TIMEOUT = 120
INIT_TIMEOUT = 600
STATUS_DLL_NOT_FOUND = 0xC0000135  # Windows: не хватает библиотеки (обычно Visual C++)

ANTIVIRUS = ("файл пропал сразу после загрузки — похоже, его удалил антивирус. Разреши ForLabs в антивирусе "
             "и нажми «Повторить».")
PORTAL = ("вместо ответа сайта пришла веб-страница — похоже, сеть просит войти (Wi-Fi колледжа). Войди в сеть "
          "в браузере и нажми «Повторить».")
TOOL_TEXTS = download.Texts(missing="сайт не отдал файл (ответ {code}) — попробуй позже или скачай по ссылке.",
                            too_big="файл больше ожидаемого — нажми «Повторить», он скачается заново.")
LINK_TEXTS = download.Texts(missing="сайт не отдал файл (ответ {code}) — проверь ссылку.",
                            too_big="файл больше 2 ГБ — такой не скачиваю.")


class ToolError(Exception):
    """Причина для студента (по-русски). help — какую кнопку показать рядом: vcredist или link."""

    def __init__(self, message: str, help: str = ""):
        super().__init__(message)
        self.help = help


# каталог

@dataclass(frozen=True)
class Tool:
    key: str
    name: str
    purpose: str
    kind: str  # portable | manual | system
    size_mb: int
    page: str = ""
    note: str = ""
    alternative: str = ""
    folder: str = ""
    exe: str = ""
    also: tuple[str, ...] = ()
    version_args: tuple[str, ...] = ()
    sources: tuple[dict, ...] = ()
    link_names: tuple[str, ...] = ()
    disk_mb: int = 0
    needs_vcredist: bool = False
    after: str = ""

    @property
    def portable(self) -> bool:
        return self.kind == "portable"


def load_catalog(path: Path = CATALOG_FILE) -> tuple[dict[str, Tool], dict]:
    data = json.loads(Path(path).read_text("utf-8"))
    tools: dict[str, Tool] = {}
    for t in data["tools"]:
        sources = t.get("sources") or ([t["source"]] if t.get("source") else [])
        tools[t["key"]] = Tool(key=t["key"], name=t["name"], purpose=t.get("for", ""), kind=t["kind"],
                               size_mb=int(t.get("size_mb", 0)), page=t.get("page", ""), note=t.get("note", ""),
                               alternative=t.get("alternative", ""), folder=t.get("folder", ""),
                               exe=t.get("exe", ""), also=tuple(t.get("also", ())),
                               version_args=tuple(t.get("version_args", ())), sources=tuple(sources),
                               link_names=tuple(t.get("link_names", ())), disk_mb=int(t.get("disk_mb", 0)),
                               needs_vcredist=bool(t.get("needs_vcredist")), after=t.get("after", ""))
        if tools[t["key"]].portable and not (tools[t["key"]].folder and tools[t["key"]].exe and sources):
            raise ValueError(f"каталог: у {t['key']} нет folder/exe/source")
    return tools, data.get("vcredist", {})


_CATALOG: tuple[dict[str, Tool], dict] | None = None


def catalog() -> dict[str, Tool]:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = load_catalog()
    return _CATALOG[0]


def how_to_get(key: str) -> str:
    """Чего не хватает и как это поставить — словами для студента (страница лабы и чат правок)."""
    t = catalog().get(key)
    if t is None:
        return ""
    what = f"«{t.name}»" + (f" (нужна для: {t.purpose})" if t.purpose else "")
    page = f" Официальная страница: {t.page}" if t.page else ""
    note = f" {t.note.rstrip('.')}." if t.note else ""
    if t.portable:
        return (f"{what}. Слева в блоке «Программа» нажми «Установить»: ForLabs сам скачает её с официального сайта "
                f"и поставит в свою папку (около {t.size_mb} МБ, права администратора не нужны). Можно поставить и "
                f"самому.{page}{note} Потом нажми «Повторить».")
    if t.kind == "system":
        return f"{what}.{note}{page} Потом нажми «Повторить»."
    return (f"{what}. ForLabs её сам не ставит: скачай установщик с официальной страницы и установи.{page}{note} "
            "Потом нажми «Повторить».")


def vcredist() -> dict:
    catalog()
    assert _CATALOG is not None
    return _CATALOG[1]


def tools_dir() -> Path:
    from forlabs.core import paths

    return paths.data_home() / "tools"


# где взять последнюю версию: по одному разбору на формат издателя

@dataclass
class Asset:
    url: str
    version: str
    hash_algo: str  # sha256 | sha512 | md5
    hash: str  # hex, строчные
    size: int | None = None

    @property
    def filename(self) -> str:
        name = unquote(PurePosixPath(urlparse(self.url).path).name)
        return _safe_name(name) or "download.zip"


def _host(url: str) -> str:
    return urlparse(url).hostname or url


def _looks_html(data: bytes) -> bool:
    head = data[:300].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html", b"<head", b"<body")) or b"<html" in head


class _Getter:
    """GET с понятными причинами сбоя. Только https, в том числе после перенаправлений."""

    def __init__(self, client):
        self.client = client
        self.last_url = ""

    def __call__(self, url: str, as_json: bool = True, headers: dict | None = None):
        import httpx

        host = _host(url)
        try:
            r = self.client.get(url, headers=headers)
        except httpx.TransportError as e:
            raise ToolError(f"нет связи с {host} ({download.net_reason(e)}) — проверь интернет и нажми «Повторить».")
        if any(h.url.scheme != "https" for h in [*r.history, r]):
            raise ToolError(f"{host} перенаправил на незащищённый адрес — не скачиваю.")
        if r.status_code in (403, 429) and r.headers.get("x-ratelimit-remaining") == "0":
            raise ToolError("GitHub временно ограничил запросы — попробуй через час или скачай по ссылке.", "link")
        if r.status_code != 200:
            if _looks_html(r.content) and r.status_code in (401, 407, 511):
                raise ToolError(PORTAL)
            raise ToolError(f"{host} не отдал список версий (ответ {r.status_code}) — попробуй позже "
                            "или скачай по ссылке.", "link")
        self.last_url = str(r.url)
        if not as_json:
            return r.text
        try:
            return r.json()
        except ValueError:
            if _looks_html(r.content):
                raise ToolError(PORTAL) from None
            raise ToolError(f"{host} прислал непонятный ответ — попробуй позже или скачай по ссылке.", "link") \
                from None


def _hex(value: str, length: int) -> str:
    v = str(value or "").strip().lower()
    if not re.fullmatch(f"[0-9a-f]{{{length}}}", v):
        raise LookupError(f"нет контрольной суммы: {value!r}")
    return v


def _int_version(v: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", str(v or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _resolve_dotnet(get: _Getter, src: dict) -> Asset:
    index = get(src["index"])
    channel = next(c for c in index["releases-index"] if c["channel-version"] == src["channel"])
    releases = get(channel["releases.json"])
    for rel in releases["releases"]:  # от новых к старым
        sdk = rel.get("sdk") or {}
        for f in sdk.get("files", []):
            if f.get("name") == src["file"]:
                return Asset(url=f["url"], version=sdk["version"], hash_algo="sha512", hash=_hex(f["hash"], 128))
    raise LookupError("нет файла SDK")


def _resolve_pymanager(get: _Getter, src: dict) -> Asset:
    index = get(src["index"])
    pick = re.compile(src["id"])
    best = None
    for v in index["versions"]:
        ver = _int_version(v.get("sort-version", ""))  # 3.15.0a1 и т.п. — не выпуск, пропускаем
        sha = (v.get("hash") or {}).get("sha256")
        if ver and sha and pick.match(str(v.get("id", ""))) and (best is None or ver > best[0]):
            best = (ver, v, sha)
    if best is None:
        raise LookupError("нет подходящей версии")
    ver, v, sha = best
    return Asset(url=urljoin(src["index"], v["url"]), version=".".join(map(str, ver)), hash_algo="sha256",
                 hash=_hex(sha, 64))


def _resolve_nuget(get: _Getter, src: dict) -> Asset:
    index = get(src["registration"])
    for page in reversed(index["items"]):  # страницы и версии в них — по возрастанию
        items = page.get("items") or get(page["@id"])["items"]
        for item in reversed(items):
            entry = item["catalogEntry"]
            if "-" in entry["version"] or not entry.get("listed", True):
                continue
            details = get(entry["@id"])
            if str(details.get("packageHashAlgorithm", "")).upper() != "SHA512":
                raise LookupError("неизвестная сумма пакета")
            digest = base64.b64decode(details["packageHash"]).hex()
            return Asset(url=item.get("packageContent") or entry["packageContent"], version=entry["version"],
                         hash_algo="sha512", hash=_hex(digest, 128), size=details.get("packageSize"))
    raise LookupError("нет выпуска")


def _sum_from_list(text: str, name: str) -> str:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return _hex(parts[0], 64)
    raise LookupError(f"нет суммы для {name}")


def _resolve_node(get: _Getter, src: dict) -> Asset:
    for rel in get(src["index"]):  # от новых к старым; первая LTS с zip для Windows
        v = rel["version"]
        if rel.get("lts") and "win-x64-zip" in rel.get("files", []) and re.fullmatch(r"v\d+\.\d+\.\d+", v):
            folder = urljoin(src["dist"], v + "/")
            name = f"node-{v}-win-x64.zip"
            return Asset(url=folder + name, version=v, hash_algo="sha256",
                         hash=_sum_from_list(get(folder + "SHASUMS256.txt", as_json=False), name))
    raise LookupError("нет LTS")


def _resolve_go(get: _Getter, src: dict) -> Asset:
    for rel in get(src["index"]):
        if not rel.get("stable"):
            continue
        for f in rel["files"]:
            if (f.get("os"), f.get("arch"), f.get("kind")) == ("windows", "amd64", "archive") \
                    and f["filename"].endswith(".zip"):
                return Asset(url=urljoin(src["dl"], f["filename"]), version=rel["version"], hash_algo="sha256",
                             hash=_hex(f["sha256"], 64), size=f.get("size"))
    raise LookupError("нет zip для Windows")


def _resolve_adoptium(get: _Getter, src: dict) -> Asset:
    for item in get(src["api"]):
        b = item["binary"]
        pkg = b["package"]
        if (b.get("os"), b.get("architecture"), b.get("image_type")) == ("windows", "x64", "jdk") \
                and pkg["name"].endswith(".zip"):
            return Asset(url=pkg["link"], version=item.get("release_name") or item["version"]["semver"],
                         hash_algo="sha256", hash=_hex(pkg["checksum"], 64), size=pkg.get("size"))
    raise LookupError("нет zip для Windows")


def _resolve_php(get: _Getter, src: dict) -> Asset:
    data = get(src["index"])
    base = get.last_url or src["index"]  # releases.json мог переехать — файлы лежат рядом с ним
    pick = re.compile(src["build"])
    best = None
    for series, info in data.items():
        ver = _int_version(series)
        if not ver or not isinstance(info, dict):
            continue
        for build, files in info.items():
            if pick.match(build) and isinstance(files, dict) and "zip" in files:
                rank = (ver, build)
                if best is None or rank > best[0]:
                    best = (rank, info["version"], files["zip"])
    if best is None:
        raise LookupError("нет сборки NTS x64")
    _, version, z = best
    return Asset(url=urljoin(base, z["path"]), version=version, hash_algo="sha256", hash=_hex(z["sha256"], 64))


def _resolve_github(get: _Getter, src: dict) -> Asset:
    releases = get(src["api"], headers={"Accept": "application/vnd.github+json"})
    patterns = [re.compile(p) for p in src["assets"]]
    for rel in releases:  # от новых к старым
        if rel.get("draft") or rel.get("prerelease"):
            continue
        assets = rel.get("assets", [])
        for rx in patterns:
            for a in assets:
                if not rx.match(a["name"]):
                    continue
                side = next((s for s in assets if s["name"] == a["name"] + ".sha256"), None)
                sha = None
                if side:  # файл суммы от автора выпуска
                    m = re.search(r"\b[0-9a-fA-F]{64}\b", get(side["browser_download_url"], as_json=False))
                    sha = m.group(0) if m else None
                if not sha and str(a.get("digest", "")).startswith("sha256:"):  # сумма, которую считает GitHub
                    sha = a["digest"][7:]
                if sha:
                    return Asset(url=a["browser_download_url"], version=rel.get("tag_name") or rel.get("name", ""),
                                 hash_algo="sha256", hash=_hex(sha, 64), size=a.get("size"))
    raise LookupError("нет подходящего архива")


def _resolve_mysql(get: _Getter, src: dict) -> Asset:
    """У MySQL нет списка версий в JSON — разбираем страницу загрузки: имя zip и MD5 под ним."""
    html = get(src["page"], as_json=False)
    for m in re.finditer(r"mysql-(\d+\.\d+\.\d+)-winx64\.zip", html):
        version = m.group(1)
        if not version.startswith(src["series"] + "."):
            continue
        tail = html[m.end():m.end() + 4000]
        nxt = re.search(r"-winx64[\w.-]*\.zip", tail)  # следующий файл таблицы — граница
        md5 = re.search(r"(?is)md5.{0,80}?\b([0-9a-f]{32})\b", tail[:nxt.start()] if nxt else tail)
        if md5:
            name = f"mysql-{version}-winx64.zip"
            return Asset(url=src["download"].format(series=src["series"], file=name), version=version,
                         hash_algo="md5", hash=md5.group(1))
    raise LookupError("нет zip для Windows с MD5")


RESOLVERS: dict[str, Callable[[_Getter, dict], Asset]] = {
    "dotnet": _resolve_dotnet, "pymanager": _resolve_pymanager, "nuget": _resolve_nuget, "node": _resolve_node,
    "go": _resolve_go, "adoptium": _resolve_adoptium, "php": _resolve_php, "github": _resolve_github,
    "mysql": _resolve_mysql,
}


# распаковка

_RESERVED = re.compile(r"(con|prn|aux|nul|com\d|lpt\d)(\..*)?", re.I)


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:150]
    return "_" + name if _RESERVED.fullmatch(name) else name


def zip_prefix(names: list[str], exe: str) -> str | None:
    """Папка внутри архива, где лежит программа: «go/» для go/bin/go.exe, «» — если в корне. Самый короткий путь
    (в пакете Python есть и Lib/venv/…/python.exe)."""
    exe = exe.strip("/")
    hits = [n for n in names if n == exe or n.endswith("/" + exe)]
    if not hits:
        return None
    best = min(hits, key=lambda n: (n.count("/"), len(n)))
    prefix = best[: len(best) - len(exe)]
    return prefix if prefix.count("/") <= 2 else None


def _zip_names(zf: zipfile.ZipFile) -> list[str]:
    return [i.filename.replace("\\", "/") for i in zf.infolist()]


def safe_extract(zf: zipfile.ZipFile, dest: Path, prefix: str = "", limit: int = UNPACK_LIMIT) -> int:
    """Распаковать часть архива под prefix в dest. Пути за пределы папки, ссылки, устройства Windows, «zip-бомбы»
    и слишком большой архив — ToolError до записи первого файла. Возвращает распакованный объём."""
    members: list[tuple[zipfile.ZipInfo, list[str]]] = []
    total = 0
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        every = [p for p in name.split("/") if p not in ("", ".")]  # весь архив, а не только нужная папка
        if name.startswith("/") or re.match(r"[A-Za-z]:", name) or ".." in every \
                or any(":" in p or _RESERVED.fullmatch(p) for p in every):
            raise ToolError("в архиве опасные пути (за пределы папки) — не распаковываю.")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ToolError("в архиве ссылки на другие папки — не распаковываю.")
        if not name.startswith(prefix):
            continue
        parts = [p for p in name[len(prefix):].split("/") if p not in ("", ".")]
        if not parts:
            continue
        total += info.file_size
        if total > limit:
            raise ToolError(f"архив распаковывается больше чем в {limit // 2**30} ГБ — не распаковываю.")
        if info.file_size > 50 * MB and info.file_size > 200 * max(info.compress_size, 1):
            raise ToolError("архив подозрительно сильно сжат — не распаковываю.")
        members.append((info, parts))
    if len(members) > MAX_ENTRIES:
        raise ToolError("в архиве слишком много файлов — не распаковываю.")
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    free = shutil.disk_usage(dest).free
    if free < total + 50 * MB:
        raise OSError(errno.ENOSPC, f"нужно {total // MB} МБ")
    for info, parts in members:
        target = dest.joinpath(*parts)
        if not target.resolve().is_relative_to(root):
            raise ToolError("в архиве опасные пути (за пределы папки) — не распаковываю.")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        left = info.file_size
        with zf.open(info) as src, open(target, "wb") as out:
            while chunk := src.read(1 << 20):
                left -= len(chunk)
                if left < 0:  # архив врёт о размере
                    raise ToolError("архив повреждён — не распаковываю.")
                out.write(chunk)
    return total


# запуск программ

def _run(cmd: list[str], timeout: float = VERSION_TIMEOUT, cwd: str | None = None) -> tuple[int | None, str]:
    """(код, вывод); None — не ответила за timeout. Окно консоли не показывается."""
    env = {**os.environ, "DOTNET_NOLOGO": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd, env=env,  # noqa: S603
                           creationflags=flags, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, ""
    except FileNotFoundError:
        return -2, "нет файла"
    except OSError as e:  # Windows: 225/226 — файл заблокировал антивирус
        return (-3 if getattr(e, "winerror", None) in (225, 226) else -1), str(e)
    out = (p.stdout or b"") + b"\n" + (p.stderr or b"")
    return p.returncode, out.decode("utf-8", "replace")


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")[:120]


def _open_file(path: str) -> None:
    """Открыть файл, как двойным щелчком в Проводнике: установщик дальше ведёт сам студент."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", path])  # noqa: S603, S607


def downloads_dir() -> Path:
    """Папка «Загрузки» (Windows может держать её на другом диске)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                            ("d", ctypes.c_ubyte * 8)]

            folder = GUID(0x374DE290, 0x123F, 0x4565, (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E,
                                                                            0x46, 0x7B))
            ptr = ctypes.c_void_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(ptr)) == 0:
                try:
                    return Path(ctypes.wstring_at(ptr.value))
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(ptr)
        except Exception:
            pass
    return Path.home() / "Downloads"


def _mark_from_internet(path: Path, url: str) -> None:
    """Метка «скачано из интернета» (как у браузера): SmartScreen проверит установщик при открытии."""
    if sys.platform != "win32":
        return
    try:
        with open(f"{path}:Zone.Identifier", "w", encoding="ascii", errors="ignore", newline="") as f:
            f.write(f"[ZoneTransfer]\r\nZoneId=3\r\nHostUrl={url}\r\n")
    except OSError:
        pass


def _retry_fs(action: Callable[[], None], what: str) -> None:
    """Переименование, которое антивирус или индексатор на миг не дают сделать."""
    for pause in (0.2, 0.5, 1.0, 2.0, 0):
        try:
            action()
            return
        except PermissionError:
            if not pause:
                raise ToolError(f"Windows не даёт {what} — файлы держит антивирус или открытая программа. "
                                "Подожди минуту и нажми «Повторить».") from None
            time.sleep(pause)


def _space_error(need_mb: int) -> ToolError:
    return ToolError(f"на диске мало места — нужно около {need_mb} МБ свободного. Освободи место и нажми «Повторить».")


# состояние для окна

@dataclass
class Job:
    state: str = "idle"  # idle | downloading | installing | done | error
    done: int = 0
    total: int | None = None
    note: str = ""
    error: str = ""
    help: str = ""  # vcredist | link — кнопка рядом с ошибкой
    version: str = ""
    path: str = ""


@dataclass
class LinkJob:
    state: str = "idle"  # idle | ready (проверена, ждёт «Скачать») | downloading | done | error
    url: str = ""
    host: str = ""
    final_host: str = ""
    filename: str = ""
    size: int | None = None
    done: int = 0
    note: str = ""
    error: str = ""
    path: str = ""
    key: str = ""  # что ждали по этой ссылке
    unpack: str = ""  # архив какой программы можно распаковать в tools
    opened: bool = False


# менеджер

def _default_http():
    import httpx

    from forlabs.version import __version__

    return httpx.Client(follow_redirects=True, timeout=httpx.Timeout(20.0, read=60.0),
                        headers={"User-Agent": f"ForLabs/{__version__} (Windows NT 10.0; Win64; x64)"})


class ToolManager:
    """Установка программ и загрузки по ссылке. Одна установка каждой программы одновременно; разные — можно."""

    def __init__(self, root: Path | None = None, http: Callable | None = None, downloads: Path | None = None,
                 open_file: Callable[[str], None] | None = None, detect: Callable | None = None,
                 run: Callable | None = None):
        self.root = Path(root) if root else tools_dir()
        self._http = http or _default_http
        self._downloads = Path(downloads) if downloads else None
        self.open_file = open_file or _open_file
        self._detect = detect
        self.run = run or _run
        self.pauses = RETRY_PAUSES
        self.tools = catalog()
        self.vcredist = vcredist()
        self.jobs = {k: Job() for k in self.tools}
        self.link = LinkJob()
        self._lock = threading.Lock()
        self._found: dict[str, dict] | None = None
        self._cleaned = False
        self._clean_lock = threading.Lock()
        self.free_space = lambda p: shutil.disk_usage(p).free  # тесты подменяют: полный диск
        self.opener = open  # и запись .part (нет места, антивирус)

    # что уже есть
    def exe(self, key: str) -> Path:
        t = self.tools[key]
        return self.root / t.folder / t.exe

    def installed(self, key: str) -> bool:
        t = self.tools[key]
        return t.portable and all((self.root / t.folder / p).is_file() for p in (t.exe, *t.also))

    def found(self, refresh: bool = False) -> dict[str, dict]:
        """{ключ: {path, version}} — что нашлось на компьютере (languages.detect: PATH, обычные папки, tools)."""
        if self._found is not None and not refresh:
            return self._found
        result: dict[str, dict] = {}
        try:
            detect = self._detect
            if detect is None:
                from forlabs.runner import languages  # пишется отдельно; пока его нет — смотрим только tools

                detect = languages.detect
            for key, tc in (detect(tools_dir=self.root) or {}).items():
                result[key] = {"path": str(getattr(tc, "path", "") or ""), "version": str(getattr(tc, "version", "")
                                                                                          or "")}
        except Exception as e:  # noqa: BLE001 — поиск не должен ломать страницу настроек
            if not isinstance(e, ImportError):
                log.warning("Поиск программ для лаб не удался: %s", e)
            for key in self.tools:
                if self.installed(key):
                    result[key] = {"path": str(self.exe(key)), "version": ""}
        self._found = result
        return result

    def cleanup(self) -> None:
        """Черновые папки прерванной установки (ForLabs закрыли посреди) — удалить."""
        with self._clean_lock:  # до первой своей черновой папки: чужих (прерванных) в этот запуск уже не будет
            if self._cleaned:
                return
            for d in [*self.root.glob(".st-*"), *self.root.glob(".old-*")]:
                shutil.rmtree(d, ignore_errors=True)
            self._cleaned = True

    # состояние
    def tool_status(self, key: str) -> dict:
        t, job = self.tools[key], self.jobs[key]
        f = self.found().get(key)
        percent = int(100 * job.done / job.total) if job.total else None
        if job.state in ("installing", "done"):
            percent = 100
        alt = self.tools.get(t.alternative)
        return {"key": key, "name": t.name, "for": t.purpose, "kind": t.kind, "size_mb": t.size_mb,
                "page": t.page, "note": t.note, "alternative": t.alternative, "alt_name": alt.name if alt else "",
                "alt_size_mb": alt.size_mb if alt else 0, "found": bool(f), "path": (f or {}).get("path", ""),
                "version": (f or {}).get("version", ""), "state": job.state, "percent": percent,
                "done_mb": job.done // MB, "note_now": job.note, "error": job.error, "help": job.help,
                "installed_version": job.version}

    def link_status(self) -> dict:
        j = self.link
        return {"state": j.state, "url": j.url, "host": j.host, "final_host": j.final_host, "filename": j.filename,
                "size_mb": round(j.size / MB, 1) if j.size else None, "done_mb": round(j.done / MB, 1),
                "percent": int(100 * j.done / j.size) if j.size else None, "note": j.note, "error": j.error,
                "path": j.path, "key": j.key, "unpack": j.unpack,
                "unpack_name": self.tools[j.unpack].name if j.unpack else "", "opened": j.opened}

    def status(self, refresh: bool = False) -> dict:
        if refresh:
            self.found(refresh=True)
        return {"tools": {k: self.tool_status(k) for k in self.tools}, "link": self.link_status(),
                "vcredist": self.vcredist}

    def _set(self, key: str, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self.jobs[key], k, v)

    def _fail(self, key: str, message: str, help: str = "") -> None:
        log.warning("Программа %s: %s", key, message)
        msg = message[:1].upper() + message[1:]
        self._set(key, state="error", error=msg, help=help, note="")

    # установка из каталога
    def start_install(self, key: str) -> bool:
        if not self._claim(key):
            return True  # уже ставится — второй клик ничего не начинает
        threading.Thread(target=self._install, args=(key,), daemon=True).start()
        return True

    def install(self, key: str) -> Job:
        """Синхронно (тесты и сценарии)."""
        if self._claim(key):
            self._install(key)
        return self.jobs[key]

    def _claim(self, key: str) -> bool:
        if not self.tools[key].portable:
            raise ToolError(f"{self.tools[key].name} ForLabs сам не ставит — открой сайт.")
        with self._lock:
            job = self.jobs[key]
            if job.state in ("downloading", "installing"):
                return False
            self.jobs[key] = Job(state="downloading", note="Узнаю последнюю версию…")
            return True

    def resolve(self, key: str) -> Asset:
        """Последняя версия у издателя: источники по порядку, первый ответивший."""
        tool = self.tools[key]
        error: ToolError | None = None
        with self._http() as client:
            get = _Getter(client)
            for src in tool.sources:
                try:
                    return RESOLVERS[src["type"]](get, src)
                except ToolError as e:
                    error = e
                except (LookupError, KeyError, TypeError, ValueError, AttributeError, StopIteration) as e:
                    log.warning("Каталог %s (%s): формат изменился: %r", key, src.get("type"), e)
                    url = next((v for v in src.values() if str(v).startswith("https://")), "")
                    error = ToolError(f"не нашёл {tool.name} на {_host(url)} — сайт мог измениться. "
                                      "Скачай по ссылке.", "link")
        raise error or ToolError("нет источника")

    def _install(self, key: str) -> None:
        tool = self.tools[key]
        try:
            self.cleanup()
            if self.installed(key):  # уже стоит (например, поставили до перезапуска) — только проверить
                try:
                    self._done(key, self._check(tool, self.root / tool.folder))
                    return
                except ToolError as e:
                    if e.help == "vcredist":
                        raise
                    log.warning("%s в tools не запускается — ставлю заново: %s", key, e)
                    self._set(key, state="downloading", note="Узнаю последнюю версию…")
            asset = self.resolve(key)
            self._need_space(tool, asset)
            archive = self._fetch(key, asset)
            self._set(key, state="installing", note="Распаковываю…")
            self._unpack(tool, archive)
            archive.unlink(missing_ok=True)
        except ToolError as e:
            self._fail(key, str(e), e.help)
        except OSError as e:
            if getattr(e, "errno", None) == errno.ENOSPC:
                self._fail(key, str(_space_error(tool.size_mb + tool.disk_mb)))
            else:
                self._fail(key, f"не удалось записать файлы ({e}). Нажми «Повторить».")
        except Exception as e:  # непредвиденное — ошибкой в строке программы, а не тихой остановкой
            log.exception("Сбой установки %s", key)
            self._fail(key, f"установка не удалась ({type(e).__name__}). Нажми «Повторить».")

    def _done(self, key: str, version: str) -> None:
        self._found = None  # «есть / нет» пересчитается
        self._set(key, state="done", note="", error="", help="", version=version, path=str(self.exe(key)))

    def _need_space(self, tool: Tool, asset: Asset) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        need = (asset.size or tool.size_mb * MB) // MB + tool.disk_mb
        if self.free_space(self.root) < need * MB:
            raise _space_error(need)

    def _progress(self, key: str, done: int, total: int | None) -> None:
        job = self.jobs[key]
        if done > job.done:  # байты снова идут — «связь прервалась…» больше не нужно
            job.note = ""
        job.done, job.total = done, total

    def _fetch(self, key: str, asset: Asset) -> Path:
        folder = self.root / ".downloads"
        folder.mkdir(parents=True, exist_ok=True)
        final = folder / f"{key}-{asset.filename}"
        part = final.with_name(final.name + ".part")
        for old in folder.glob(f"{key}-*"):  # прошлые версии; своя недокачанная — докачивается
            if old not in (final, part):
                old.unlink(missing_ok=True)
        if final.exists() and self._hash_ok(final, asset):
            return final
        final.unlink(missing_ok=True)

        def note(attempt: int) -> None:
            self.jobs[key].note = f"Связь прервалась — продолжаю загрузку (попытка {attempt + 1})…"

        try:
            download.fetch_retrying(self._http, asset.url, part, pauses=self.pauses, on_retry=note,
                                    size=asset.size, limit=TOOL_LIMIT, looks_right=lambda b: b[:2] == b"PK",
                                    progress=lambda d, t: self._progress(key, d, t), texts=TOOL_TEXTS,
                                    opener=self.opener)
        except Retry as e:
            raise ToolError(f"не удалось скачать: {e}. Проверь интернет и нажми «Повторить» — загрузка "
                            "продолжится с того же места.") from None
        except Stop as e:
            part.unlink(missing_ok=True)
            raise ToolError(str(e)) from None
        if not part.exists():
            raise ToolError(ANTIVIRUS)
        if not self._hash_ok(part, asset):
            part.unlink(missing_ok=True)
            raise ToolError("файл повреждён при загрузке — нажми «Повторить», он скачается заново.")
        _retry_fs(lambda: os.replace(part, final), "сохранить файл")
        return final

    @staticmethod
    def _hash_ok(path: Path, asset: Asset) -> bool:
        digest = hashlib.new(asset.hash_algo)
        for pause in (0.5, 1.0, 2.0, 0):
            try:
                with open(path, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        digest.update(chunk)
                break
            except FileNotFoundError:
                raise ToolError(ANTIVIRUS) from None
            except PermissionError:  # антивирус ещё проверяет файл
                if not pause:
                    raise ToolError("антивирус не отдаёт скачанный файл. Подожди минуту и нажми «Повторить».") \
                        from None
                digest = hashlib.new(asset.hash_algo)
                time.sleep(pause)
        return digest.hexdigest() == asset.hash.lower()

    def _unpack(self, tool: Tool, archive: Path, ours: bool = True) -> None:
        """Архив → черновая папка → пробный запуск и доводка → tools/<folder> одним переименованием.
        ours=False — файл студента из «Загрузок»: битый не удаляем."""
        stage = self.root / f".st-{tool.key}-{uuid.uuid4().hex[:6]}"
        try:
            try:
                with zipfile.ZipFile(archive) as zf:
                    prefix = zip_prefix(_zip_names(zf), tool.exe)
                    if prefix is None:
                        raise ToolError(f"в архиве нет {PurePosixPath(tool.exe).name} — это не {tool.name}.")
                    safe_extract(zf, stage, prefix)
            except (zipfile.BadZipFile, EOFError, zlib.error) as e:
                if not ours:
                    raise ToolError(f"архив повреждён ({e}) — скачай его заново.") from None
                archive.unlink(missing_ok=True)
                raise ToolError(f"архив повреждён ({e}) — нажми «Повторить», он скачается заново.") from None
            if not stage.exists():
                stage.mkdir(parents=True)
            missing = [p for p in (tool.exe, *tool.also) if not (stage / p).is_file()]
            if missing:
                raise ToolError("после распаковки пропали файлы программы — похоже, их удалил антивирус. "
                                "Разреши ForLabs в антивирусе и нажми «Повторить».")
            version = self._check(tool, stage)
            if tool.after:
                AFTER[tool.after](self, tool, stage)
            final = self.root / tool.folder
            old = None
            if final.exists():  # сломанная прежняя копия
                old = self.root / f".old-{tool.key}-{uuid.uuid4().hex[:6]}"
                _retry_fs(lambda: os.replace(final, old), "заменить прежнюю копию")
            try:
                _retry_fs(lambda: os.replace(stage, final), "переместить программу на место")
            except ToolError:
                if old is not None and not final.exists():
                    os.replace(old, final)
                raise
            if old is not None:
                shutil.rmtree(old, ignore_errors=True)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        self._done(tool.key, version)

    def _check(self, tool: Tool, base: Path) -> str:
        """Пробный запуск (`go version`); причина отказа — по-русски."""
        exe = base / tool.exe
        if not exe.is_file():
            raise ToolError(ANTIVIRUS)
        self._set(tool.key, state="installing", note="Проверяю, что запускается…")
        code, out = self.run([str(exe), *tool.version_args])
        if code == 0:
            return _first_line(out)
        if code is not None and code & 0xFFFFFFFF == STATUS_DLL_NOT_FOUND:
            raise ToolError(f"{tool.name} не запускается: в Windows нет «Visual C++». Скачай его по кнопке, "
                            "установи и нажми «Повторить».", "vcredist")
        if code is None:
            raise ToolError(f"{tool.name}: нет ответа за {VERSION_TIMEOUT // 60} мин. Нажми «Повторить».")
        if code in (-2, -3):
            raise ToolError("программу заблокировал или удалил антивирус. Разреши ForLabs в антивирусе и "
                            "нажми «Повторить».")
        log.warning("Пробный запуск %s: код %s: %s", tool.key, code, out[-500:])
        raise ToolError(f"{tool.name} не запускается (код {code}). Нажми «Повторить» или скачай по ссылке.", "link")

    # своя ссылка
    def downloads(self) -> Path:
        return self._downloads or downloads_dir()

    def check_link(self, url: str, key: str | None = None) -> dict:
        """Узнать сайт, имя и размер файла — до загрузки. Без https и больше 2 ГБ — отказ."""
        import httpx

        url = (url or "").strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ToolError("Нужна ссылка, которая начинается с https://")
        if len(url) > 2000:
            raise ToolError("Слишком длинная ссылка.")
        with self._lock:
            if self.link.state == "downloading":
                raise ToolError("Сейчас уже качается файл — дождись его.")
        try:
            with self._http() as client, client.stream("GET", url) as r:
                hops = [*r.history, r]
                final, status = str(r.url), r.status_code
                headers = r.headers
        except httpx.TransportError as e:
            raise ToolError(f"Нет связи с {parsed.hostname} ({download.net_reason(e)}).") from None
        if any(h.url.scheme != "https" for h in hops):
            raise ToolError("Ссылка ведёт на незащищённый адрес (http) — такой файл не скачиваю.")
        if status != 200:
            raise ToolError(f"Сайт не отдал файл (ответ {status}) — проверь ссылку.")
        if headers.get("content-type", "").lower().startswith("text/html"):
            raise ToolError("По ссылке страница, а не файл. Открой её в браузере и скопируй ссылку на сам файл "
                            "(кнопка «Скачать»).")
        length = headers.get("content-length", "")
        size = int(length) if length.isdigit() else None
        if size and size > LINK_LIMIT:
            raise ToolError("Файл больше 2 ГБ — такой не скачиваю.")
        name = _file_name(headers.get("content-disposition", ""), final)
        with self._lock:
            self.link = LinkJob(state="ready", url=url, host=parsed.hostname, final_host=_host(final),
                                filename=name, size=size, key=key if key in self.tools else "")
        return self.link_status()

    def start_link(self, url: str, key: str | None = None, wait: bool = False) -> dict:
        """Скачать проверенную ссылку в «Загрузки». Ссылка не та, что проверяли, — сначала проверить."""
        url = (url or "").strip()
        with self._lock:
            j = self.link
            if j.state == "downloading":
                return self.link_status()
            ready = j.url == url and j.state in ("ready", "error", "done")
        if not ready:
            return self.check_link(url, key)
        with self._lock:
            self.link = LinkJob(state="downloading", url=j.url, host=j.host, final_host=j.final_host,
                                filename=j.filename, size=j.size, key=j.key)
        if wait:
            self._download_link()
        else:
            threading.Thread(target=self._download_link, daemon=True).start()
        return self.link_status()

    def _link_fail(self, message: str) -> None:
        log.warning("Загрузка по ссылке: %s", message)
        with self._lock:
            self.link.state, self.link.error, self.link.note = "error", message[:1].upper() + message[1:], ""

    def _download_link(self) -> None:
        j = self.link
        try:
            folder = self.downloads()
            folder.mkdir(parents=True, exist_ok=True)
            tag = hashlib.sha1(j.url.encode()).hexdigest()[:8]  # noqa: S324 — имя черновика, не защита
            part = folder / f"{j.filename}.{tag}.part"  # та же ссылка после перезапуска — докачивается

            def progress(done: int, total: int | None) -> None:
                j.done, j.note = done, ""
                if total:
                    j.size = total

            def note(attempt: int) -> None:
                j.note = f"Связь прервалась — продолжаю загрузку (попытка {attempt + 1})…"

            try:
                download.fetch_retrying(self._http, j.url, part, pauses=self.pauses, on_retry=note, limit=LINK_LIMIT,
                                        looks_right=lambda b: not _looks_html(b), progress=progress,
                                        texts=LINK_TEXTS, opener=self.opener)
            except Retry as e:
                self._link_fail(f"не удалось скачать: {e}. Проверь интернет и нажми «Скачать» ещё раз — загрузка "
                                "продолжится с того же места.")
                return
            except Stop as e:
                part.unlink(missing_ok=True)
                self._link_fail(str(e))
                return
            if not part.exists():
                self._link_fail(ANTIVIRUS)
                return
            target = _unique(folder / j.filename)
            _retry_fs(lambda: os.replace(part, target), "сохранить файл")
            _mark_from_internet(target, j.url)
            unpack = self._zip_tool(target, j.key)
            with self._lock:
                j.state, j.path, j.unpack, j.note, j.done = "done", str(target), unpack, "", target.stat().st_size
            if not unpack:  # установщик (или что угодно) — открывает студент, дальше он сам
                self.open_link()
        except ToolError as e:
            self._link_fail(str(e))
        except OSError as e:
            if getattr(e, "errno", None) == errno.ENOSPC:
                self._link_fail(f"на диске мало места — нужно около {max(1, (j.size or 0) // MB)} МБ.")
            else:
                self._link_fail(f"не удалось сохранить файл ({e}).")
        except Exception as e:
            log.exception("Сбой загрузки по ссылке")
            self._link_fail(f"не удалось скачать ({type(e).__name__}).")

    def open_link(self) -> bool:
        path = self.link.path
        if not path or not Path(path).exists():
            return False
        try:
            self.open_file(path)
        except OSError as e:
            self.link.note = f"Не открылся: {e}. Открой его сам в папке «Загрузки»."
            return False
        self.link.opened = True
        return True

    def _zip_tool(self, path: Path, hint: str = "") -> str:
        """Архив известной программы (по содержимому): какой? Пусто — не архив или не наша программа."""
        if path.suffix.lower() not in (".zip", ".nupkg"):
            return ""
        try:
            with zipfile.ZipFile(path) as zf:
                names = _zip_names(zf)
        except (zipfile.BadZipFile, OSError):
            return ""
        order = [hint] if hint in self.tools else []
        order += [k for k, t in self.tools.items() if any(re.match(p, path.name, re.I) for p in t.link_names)]
        order += list(self.tools)
        for key in order:
            t = self.tools[key]
            if t.portable and zip_prefix(names, t.exe) is not None \
                    and all(zip_prefix(names, p) is not None for p in t.also):
                return key
        return ""

    def install_from_link(self, wait: bool = False) -> bool:
        """Скачанный по ссылке архив программы — в tools (без контрольной суммы: ссылку дал студент)."""
        key, path = self.link.unpack, Path(self.link.path or ".")
        if not key or not path.is_file():
            return False
        if not self._claim(key):
            return True
        self._set(key, state="installing", note="Распаковываю…")

        def job() -> None:
            tool = self.tools[key]
            try:
                self.cleanup()
                self._unpack(tool, path, ours=False)
            except ToolError as e:
                self._fail(key, str(e), e.help)
            except OSError as e:
                self._fail(key, str(_space_error(tool.disk_mb)) if getattr(e, "errno", None) == errno.ENOSPC
                           else f"не удалось записать файлы ({e}).")
            except Exception as e:
                log.exception("Сбой распаковки %s", key)
                self._fail(key, f"распаковка не удалась ({type(e).__name__}).")

        if wait:
            job()
        else:
            threading.Thread(target=job, daemon=True).start()
        return True


def _file_name(disposition: str, url: str) -> str:
    m = re.search(r"filename\*\s*=\s*[\w-]+''([^;]+)", disposition, re.I)
    name = unquote(m.group(1)) if m else ""
    if not name:
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', disposition, re.I)
        name = m.group(1) if m else unquote(PurePosixPath(urlparse(url).path).name)
    return _safe_name(PurePosixPath(name.replace("\\", "/")).name) or "файл"


def _unique(path: Path) -> Path:
    """Не перезаписывать файл студента: «setup (1).exe»."""
    if not path.exists():
        return path
    for i in range(1, 1000):
        cand = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not cand.exists():
            return cand
    return path.with_name(f"{path.stem} ({uuid.uuid4().hex[:6]}){path.suffix}")


# доводка после распаковки (в черновой папке, до переноса на место)

def _after_php_ini(mgr: ToolManager, tool: Tool, base: Path) -> None:
    """php.ini из php.ini-development: папка расширений и mysqli/pdo_mysql/mbstring/openssl для лаб."""
    ini, dev = base / "php.ini", base / "php.ini-development"
    if ini.exists() or not dev.exists():
        return
    text = dev.read_text("utf-8", "replace")
    ext = (mgr.root / tool.folder / "ext").as_posix()
    text = re.sub(r'^;\s*extension_dir\s*=\s*"ext"\s*$', f'extension_dir = "{ext}"', text, count=1, flags=re.M)
    for name in ("mysqli", "pdo_mysql", "mbstring", "openssl", "curl", "fileinfo"):
        text = re.sub(rf"^;\s*(extension\s*=\s*{name})\s*$", r"\1", text, flags=re.M)
    ini.write_text(text, "utf-8")


def _after_mysql_init(mgr: ToolManager, tool: Tool, base: Path) -> None:
    """Пустая база в mysql/data: root без пароля, слушает только этот компьютер (start_mysql)."""
    data = base / "data"
    if data.exists():
        return
    mgr._set(tool.key, note="Готовлю базу MySQL (до минуты)…")
    code, out = mgr.run([str(base / "bin" / "mysqld.exe"), "--no-defaults", f"--basedir={base}",
                         f"--datadir={data}", "--initialize-insecure", "--console"], timeout=INIT_TIMEOUT)
    if code != 0 or not data.is_dir():
        log.warning("mysqld --initialize-insecure: код %s: %s", code, out[-800:])
        shutil.rmtree(data, ignore_errors=True)
        raise ToolError(f"MySQL не подготовил базу (код {code}). Нажми «Повторить».")


AFTER: dict[str, Callable[[ToolManager, Tool, Path], None]] = {
    "php_ini": _after_php_ini, "mysql_init": _after_mysql_init,
}
