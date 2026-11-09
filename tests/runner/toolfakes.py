"""Подделки для тестов программ для лаб: сайты издателей и запуск .exe (интернет не нужен)."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx

FIX = Path(__file__).parents[1] / "fixtures" / "tools"
GO_INDEX = "https://go.dev/dl/?mode=json"


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


class Broken(httpx.SyncByteStream):
    """Отдаёт часть тела и обрывается — как пропавший Wi-Fi."""

    def __init__(self, body: bytes, exc: Exception):
        self.body, self.exc = body, exc

    def __iter__(self):
        yield self.body
        raise self.exc


class Web:
    """Интернет для тестов: адрес → ответ. Докачка (Range), обрывы, «нет сети», перенаправления."""

    def __init__(self):
        self.routes: dict[str, tuple[int, bytes, dict]] = {}
        self.calls: list[tuple[str, str | None]] = []
        self.offline: set[str] = set()  # хосты без связи; "*" — нет интернета вовсе
        self.drops: dict[str, list[int]] = {}  # адрес → на каком байте оборвать очередные загрузки
        self.gate = None  # threading.Event: держать загрузку файлов до сигнала

    def add(self, url: str, body: bytes | str, status: int = 200, headers: dict | None = None) -> None:
        self.routes[url] = (status, body.encode() if isinstance(body, str) else body, headers or {})

    def json(self, url: str, data) -> None:
        self.add(url, json.dumps(data), headers={"content-type": "application/json"})

    def redirect(self, url: str, to: str) -> None:
        self.routes[url] = (302, b"", {"location": to})

    def handler(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        rng = req.headers.get("range")
        self.calls.append((url, rng))
        if "*" in self.offline or req.url.host in self.offline:
            raise httpx.ConnectError("сеть недоступна", request=req)
        if url not in self.routes:
            return httpx.Response(404, content=b"not found")
        status, body, headers = self.routes[url]
        if status != 200:
            return httpx.Response(status, content=body, headers=headers)
        if self.gate is not None and body[:2] == b"PK":
            self.gate.wait(5)
        code, start = 200, 0
        if rng:
            start, code = int(rng.split("=")[1].rstrip("-")), 206
            headers = {**headers, "content-range": f"bytes {start}-{len(body) - 1}/{len(body)}"}
        part = body[start:]
        cuts = self.drops.get(url)
        if cuts:
            cut = cuts.pop(0)
            return httpx.Response(code, stream=Broken(part[:cut], httpx.ReadError("обрыв", request=req)),
                                  headers={**headers, "content-length": str(len(part))})
        return httpx.Response(code, content=part, headers=headers)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=True)

    def downloads(self, suffix: str) -> list[str | None]:
        return [rng for url, rng in self.calls if url.endswith(suffix)]


class Runs:
    """Запуск программ: записывает команды, «готовит» базу MySQL, отвечает заданным кодом."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.codes: dict[str, tuple[int | None, str]] = {}  # имя exe → (код, вывод)
        self.init_code = 0

    def __call__(self, cmd, timeout=None, cwd=None):
        self.calls.append([str(c) for c in cmd])
        exe = Path(cmd[0])
        if "--initialize-insecure" in cmd:
            if self.init_code == 0:
                data = next(c for c in cmd if str(c).startswith("--datadir=")).split("=", 1)[1]
                Path(data).mkdir(parents=True)
                (Path(data) / "ibdata1").write_bytes(b"\0" * 10)
            return self.init_code, "[Note] root@localhost is created with an empty password"
        if not exe.is_file():
            return -2, "нет файла"
        return self.codes.get(exe.name, (0, f"{exe.stem} версия 1.0\n"))


def go_site(web: Web, files: dict[str, bytes] | None = None, sha: str | None = None, version: str = "go1.99.1"):
    """go.dev: список версий и архив. Возвращает байты архива."""
    data = make_zip(files or {"go/bin/go.exe": b"MZ go", "go/bin/gofmt.exe": b"MZ fmt", "go/VERSION": b"go1.99.1"})
    name = f"{version}.windows-amd64.zip"
    web.json(GO_INDEX, [{"version": version, "stable": True, "files": [
        {"filename": name, "os": "windows", "arch": "amd64", "version": version, "kind": "archive",
         "sha256": sha or hashlib.sha256(data).hexdigest(), "size": len(data)}]}])
    web.add(f"https://go.dev/dl/{name}", data)
    return data
