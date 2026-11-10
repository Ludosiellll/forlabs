"""Обновление из самого приложения: новая версия берётся из выпусков на GitHub.

К каждому выпуску CI прикладывает latest.json (версия, размер, SHA-256 установщика). Приложение раз в несколько
часов смотрит его, по кнопке «Обновить» скачивает ForLabsSetup.exe в папку данных, сверяет размер и SHA-256
и запускает установщик в режиме обновления: тот ждёт, пока ForLabs закроется, ставит новую версию в ту же папку
и открывает её. Работает только в установленной программе на Windows; без связи или при закрытом репозитории
молча ничего не предлагает.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from forlabs.core import download
from forlabs.core.download import RETRY_PAUSES
from forlabs.version import __version__

REPO = "Ludosiellll/forlabs"
LATEST_JSON = f"https://github.com/{REPO}/releases/latest/download/latest.json"
DOWNLOADS = f"https://github.com/{REPO}/releases/download/"
CHECK_EVERY = 6 * 3600  # секунд между проверками
MAX_SETUP = 600 * 2**20  # установщик больше 600 МБ — это не наш файл
log = logging.getLogger("forlabs")


# докачка, повторы и портал Wi-Fi — общие с загрузкой программ для лаб (core/download.py)
_Retry, _Stop = download.Retry, download.Stop
TEXTS = download.Texts(
    portal="вместо обновления пришла веб-страница — похоже, сеть просит войти (Wi-Fi колледжа, гостиница). "
           "Войди в сеть в браузере и нажми «Повторить».",
    missing="обновление не найдено на сервере (ответ {code}) — попробуй позже.",
    too_big="файл обновления больше ожидаемого — нажми «Повторить», он скачается заново.")


def parse_version(v: str) -> tuple[int, ...]:
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", (v or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


@dataclass
class Release:
    version: str
    url: str
    size: int
    sha256: str


def _default_http():
    import httpx

    return httpx.Client(follow_redirects=True, timeout=httpx.Timeout(20.0, read=60.0),
                        headers={"User-Agent": f"ForLabs/{__version__}"})


def _enabled() -> bool:
    from forlabs.version import frozen

    return frozen() and sys.platform == "win32"


def spawn_detached(cmd: list[str]) -> None:
    """Установщик живёт дольше ForLabs: отдельный процесс вне задания (job) приложения. Рабочая папка — не папка
    программы (её задаёт ярлык ForLabs): Windows не даёт заменить папку, которая для процесса рабочая."""
    import tempfile

    cwd = tempfile.gettempdir()
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB
    try:
        subprocess.Popen(cmd, close_fds=True, creationflags=flags, cwd=cwd)  # noqa: S603
    except OSError:
        if not flags:
            raise
        subprocess.Popen(cmd, close_fds=True, cwd=cwd,  # noqa: S603
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


class Updater:
    def __init__(self, current: str = __version__, http: Callable | None = None, folder: Path | None = None,
                 enabled: bool | None = None):
        from forlabs.core import paths

        self.current = current
        self._http = http or _default_http
        self.folder = Path(folder) if folder else paths.data_home() / "updates"
        self.enabled = _enabled() if enabled is None else enabled
        self._lock = threading.Lock()
        self.release: Release | None = None
        self.installer: Path | None = None
        self.state = "idle" if self.enabled else "off"
        self.error = ""
        self.done = 0
        self.note = ""  # «связь прервалась — продолжаю…» во время повторов
        self.pauses = RETRY_PAUSES
        self.dismissed = False  # «Позже»: до следующего запуска карточка не показывается
        self.checked_at = 0.0

    # проверка
    def check(self) -> Release | None:
        if not self.enabled:
            return None
        self.checked_at = time.monotonic()
        try:
            with self._http() as http:
                r = http.get(LATEST_JSON)
                if r.status_code != 200:
                    return None
                meta = r.json()
            rel = Release(version=str(meta["version"]), url=str(meta["url"]), size=int(meta["size"]),
                          sha256=str(meta["sha256"]).lower())
        except Exception as e:  # нет связи, закрытый репозиторий, чужой ответ — просто не предлагаем
            log.info("Проверка обновления не удалась: %s", e)
            return None
        if not (parse_version(rel.version) > parse_version(self.current)):
            self._clean_old()  # уже обновились — скачанный установщик больше не нужен
            return None
        if not 0 < rel.size <= MAX_SETUP or not re.fullmatch(r"[0-9a-f]{64}", rel.sha256) \
                or not rel.url.startswith(DOWNLOADS):  # только из выпусков ForLabs на GitHub
            return None
        with self._lock:
            if self.state in ("idle", "available", "error"):
                self.release, self.state, self.error = rel, "available", ""
        return rel

    def _clean_old(self) -> None:
        try:
            for old in self.folder.glob("ForLabsSetup-*"):
                old.unlink(missing_ok=True)
        except OSError:
            pass

    def check_in_background(self) -> None:
        if self.enabled and self.state in ("idle", "available") and \
                (not self.checked_at or time.monotonic() - self.checked_at > CHECK_EVERY):
            self.checked_at = time.monotonic()
            threading.Thread(target=self.check, daemon=True).start()

    # загрузка: с докачкой после обрыва (Range), повторами и проверкой SHA-256 всего файла
    def start_download(self) -> bool:
        with self._lock:
            if self.state not in ("available", "error") or self.release is None:
                return self.state in ("downloading", "ready")
            self.state, self.error, self.note = "downloading", "", ""
        threading.Thread(target=self._download, daemon=True).start()
        return True

    def download(self) -> None:
        """Синхронно (тесты и сценарии)."""
        with self._lock:
            if self.release is None:
                return
            self.state, self.error, self.note = "downloading", "", ""
        self._download()

    def _fail(self, msg: str) -> None:
        log.warning("Обновление: %s", msg)
        with self._lock:
            self.state, self.error, self.note = "error", msg, ""

    def _download(self) -> None:
        rel = self.release
        assert rel is not None
        final = self.folder / f"ForLabsSetup-{rel.version}.exe"
        part = final.with_name(final.name + ".part")
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            for old in self.folder.glob("ForLabsSetup-*"):  # другие версии; своя недокачанная — докачивается
                if old not in (final, part):
                    old.unlink(missing_ok=True)
            if final.exists() and self._valid(final, rel):  # скачано раньше (например, до перезапуска)
                with self._lock:
                    self.installer, self.state, self.done = final, "ready", rel.size
                return
            final.unlink(missing_ok=True)

            def note(attempt: int) -> None:
                self.note = f"Связь прервалась — продолжаю загрузку (попытка {attempt + 1})…"

            try:
                download.fetch_retrying(self._http, rel.url, part, pauses=self.pauses, on_retry=note,
                                        sleep=time.sleep, size=rel.size, looks_right=lambda b: b.startswith(b"MZ"),
                                        progress=self._progress, texts=TEXTS, opener=open)
            except _Retry as e:
                self._fail(f"не удалось скачать обновление: {e}. Проверь интернет и нажми «Повторить» — "
                           "загрузка продолжится с того же места.")
                return
            self.note = ""
            if not self._valid(part, rel):
                part.unlink(missing_ok=True)
                self._fail("файл обновления повреждён при загрузке — нажми «Повторить», он скачается заново.")
                return
            part.replace(final)
            with self._lock:
                self.installer, self.state = final, "ready"
        except _Stop as e:
            part.unlink(missing_ok=True)
            self._fail(str(e))
        except OSError as e:
            if getattr(e, "errno", None) == errno.ENOSPC:
                self._fail(f"на диске не хватает места для обновления — нужно около {max(1, rel.size // 2**20)} МБ. "
                           "Освободи место и нажми «Повторить».")
            else:
                self._fail(f"не удалось сохранить обновление ({e}). Нажми «Повторить».")
        except Exception as e:  # непредвиденное — ошибкой в карточке, а не тихой остановкой
            log.exception("Сбой загрузки обновления")
            self._fail(f"не удалось скачать обновление ({type(e).__name__}). Нажми «Повторить».")

    def _progress(self, done: int, total: int | None) -> None:
        self.done = done

    @staticmethod
    def _valid(path: Path, rel: Release) -> bool:
        if path.stat().st_size != rel.size:
            return False
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            head = f.read(2)
            digest.update(head)
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        return head == b"MZ" and digest.hexdigest() == rel.sha256

    # установка
    def install(self, pid: int, spawn: Callable[[list[str]], None] = spawn_detached) -> None:
        if self.state != "ready" or self.installer is None or not self.installer.exists():
            raise RuntimeError("Обновление ещё не скачано")
        spawn([str(self.installer), "--update", "--wait-pid", str(pid)])
        self.state = "installing"

    def status(self) -> dict:
        rel = self.release
        size = rel.size if rel else 0
        return {"state": self.state, "version": rel.version if rel else "", "current": self.current,
                "percent": 100 if self.state in ("ready", "installing") else
                (int(100 * self.done / size) if size else 0),
                "size_mb": max(1, round(size / 2**20)) if size else 0, "error": self.error, "note": self.note,
                "dismissed": self.dismissed}
