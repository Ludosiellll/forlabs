"""Загрузка большого файла в .part: докачка после обрыва (Range), повторы, страница входа в Wi-Fi вместо файла.

Общая для обновления ForLabs (core/update.py) и программ для лаб (runner/tools.py). Нет места на диске (ENOSPC)
и прочие ошибки записи поднимаются как OSError — их объясняет вызывающий.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

RETRY_PAUSES = (3.0, 8.0, 20.0)  # секунд перед повторами после обрыва связи


class Retry(Exception):
    """Временный сбой связи — загрузка продолжится с того же места."""


class Stop(Exception):
    """Повтор не поможет без действия человека (портал Wi-Fi, нет файла) — недокачанное удаляется."""


def net_reason(e: Exception) -> str:
    import httpx

    if isinstance(e, httpx.TimeoutException):
        return "сервер долго не отвечал"
    if isinstance(e, httpx.ConnectError):
        return "нет связи с сервером"
    return "связь оборвалась посреди загрузки"


@dataclass(frozen=True)
class Texts:
    """Причины остановки — своими словами для каждого места (обновление, программа, файл по ссылке)."""

    portal: str = ("вместо файла пришла веб-страница — похоже, сеть просит войти (Wi-Fi колледжа, гостиница). "
                   "Войди в сеть в браузере и нажми «Повторить».")
    missing: str = "файл не найден на сервере (ответ {code}) — попробуй позже."
    too_big: str = "файл больше ожидаемого — нажми «Повторить», он скачается заново."
    insecure: str = "ссылка ведёт на незащищённый адрес (http) — такой файл не скачиваю."


def _total(r, have: int) -> int | None:
    """Полный размер файла из ответа: Content-Range у докачки, Content-Length у целого файла."""
    rng = r.headers.get("content-range", "")
    if r.status_code == 206 and "/" in rng:
        tail = rng.rsplit("/", 1)[1].strip()
        return int(tail) if tail.isdigit() else None
    length = r.headers.get("content-length", "")
    if length.isdigit():
        return int(length) + (have if r.status_code == 206 else 0)
    return None


def fetch_part(http: Callable, url: str, part: Path, *, size: int | None = None, limit: int | None = None,
               looks_right: Callable[[bytes], bool] | None = None,
               progress: Callable[[int, int | None], None] | None = None,
               texts: Texts = Texts(), opener: Callable = open) -> int:
    """Одна попытка: дописывает part с места обрыва и возвращает его размер. size — точный размер, если известен
    заранее; limit — больше этого не качать. looks_right(первый кусок) — проверка, что пришёл файл, а не страница.
    Сбой связи → Retry (part остаётся), бессмысленный повтор → Stop. opener — открытие part (тесты подменяют)."""
    import httpx

    def report(done: int, total: int | None) -> None:
        if progress:
            progress(done, total)

    have = part.stat().st_size if part.exists() else 0
    if size is not None and have > size:
        part.unlink()
        have = 0
    report(have, size)
    if size is not None and have == size:
        return have
    done, total = have, size
    try:
        with http() as client, client.stream("GET", url, headers={"Range": f"bytes={have}-"} if have else None) as r:
            if any(h.url.scheme != "https" for h in [*r.history, r]):  # и каждый переход по перенаправлению
                raise Stop(texts.insecure)
            if r.status_code == 206 and have:
                mode = "ab"
            elif r.status_code == 200:
                mode, have = "wb", 0  # сервер не умеет докачку — сначала
            elif r.status_code == 416:
                part.unlink(missing_ok=True)
                raise Retry("сервер не принял докачку")
            elif r.status_code >= 500 or r.status_code == 429:
                raise Retry(f"сервер ответил {r.status_code}")
            else:
                raise Stop(texts.missing.format(code=r.status_code))
            done = have
            total = size or _total(r, have)
            cap = size or limit
            if cap and total and total > cap:
                raise Stop(texts.too_big)
            report(done, total)
            first = mode == "wb"
            with opener(part, mode) as f:
                for chunk in r.iter_bytes():  # как пришло — при обрыве на диске всё полученное
                    if first and chunk:
                        first = False
                        if looks_right and not looks_right(chunk):
                            raise Stop(texts.portal)
                    if cap and done + len(chunk) > cap:
                        raise Stop(texts.too_big)
                    f.write(chunk)
                    done += len(chunk)
                    report(done, total)
    except httpx.TransportError as e:  # обрыв, таймаут, сеть пропала — докачаем
        raise Retry(net_reason(e)) from e
    if total is not None and done < total:
        raise Retry("связь оборвалась посреди загрузки")
    return done


def fetch_retrying(http: Callable, url: str, part: Path, *, pauses=RETRY_PAUSES,
                   on_retry: Callable[[int], None] | None = None, sleep: Callable[[float], None] = time.sleep,
                   **kw) -> int:
    """fetch_part с повторами после обрыва: каждый раз с того же места. Повторы кончились — последний Retry."""
    attempt = 0
    while True:
        try:
            return fetch_part(http, url, part, **kw)
        except Retry:
            attempt += 1
            if attempt > len(pauses):
                raise
            if on_retry:
                on_retry(attempt)
            sleep(pauses[attempt - 1])
