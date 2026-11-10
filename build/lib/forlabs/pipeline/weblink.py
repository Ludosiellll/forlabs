"""Ссылка как материал лабы: ForLabs скачивает страницу и берёт из неё текст — заголовки, абзацы, таблицы, поля
форм, кнопки и ссылки (для тест-кейсов по сайту важны именно они). Модель читает это так же, как методичку.

Ссылка на PDF или DOCX читается как файл. Скачивается только страница по ссылке (без её картинок и скриптов),
только http/https и не адреса этого компьютера и локальной сети. Защиту сайта от программ (код 403, капча) ForLabs не
обходит: просит сохранить страницу в PDF и добавить файлом."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 4 * 1024 * 1024
TIMEOUT = 25.0
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 "
              "Safari/537.36")
SKIP = {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "head"}
BLOCKS = {"p", "div", "section", "article", "main", "header", "footer", "nav", "aside", "li", "ul", "ol", "dl", "dt",
          "dd", "br", "hr", "tr", "table", "form", "fieldset", "blockquote", "pre", "figure", "figcaption", "h1", "h2",
          "h3", "h4", "h5", "h6", "label", "option", "summary", "details"}
FILE_EXT = {".pdf", ".docx", ".xlsx", ".txt", ".csv", ".json", ".xml", ".md"}


class LinkError(ValueError):
    """Страница не добавлена — причина для студента."""


def check_url(url: str) -> str:
    url = url.strip()
    if url and "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 — «javascript:alert(1)» и т.п.: порт не разбирается
    except ValueError:
        raise LinkError("нужна ссылка на страницу в интернете (http:// или https://)") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise LinkError("нужна ссылка на страницу в интернете (http:// или https://)")
    host = parts.hostname.lower()
    if host in ("localhost",) or host.endswith((".local", ".localhost", ".internal", ".lan")):
        raise LinkError("ссылки на этот компьютер и локальную сеть ForLabs не скачивает")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url
    if not ip.is_global:
        raise LinkError("ссылки на этот компьютер и локальную сеть ForLabs не скачивает")
    return url


def html_text(html: str, base: str = "") -> tuple[str, str]:
    """(заголовок, текст) страницы: блоки с новой строки, таблицы — строками через «|», поля форм и кнопки —
    в квадратных скобках, ссылки — с адресом."""
    import lxml.html

    try:
        doc = lxml.html.document_fromstring(html)
    except Exception:  # noqa: BLE001 — пустая или совсем битая страница
        return "", ""
    title = " ".join((doc.findtext(".//title") or "").split())
    out: list[str] = []

    def add(text: str) -> None:
        if text:
            out.append(text)

    def walk(el) -> None:
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag in SKIP or el.get("hidden") is not None or el.get("aria-hidden") == "true":
            if el.tail:
                add(el.tail)
            return
        if tag in BLOCKS:
            add("\n")
        if tag in ("h1", "h2", "h3", "h4"):
            add("#" * int(tag[1]) + " ")
        if tag == "li":
            add("• ")
        if tag in ("td", "th"):
            add(" | ")
        if tag == "input":
            kind = (el.get("type") or "text").lower()
            if kind not in ("hidden",):
                name = el.get("placeholder") or el.get("aria-label") or el.get("name") or el.get("id") or ""
                label = "кнопка" if kind in ("submit", "button", "reset") else f"поле {kind}"
                value = el.get("value") if kind in ("submit", "button", "reset") else ""
                add(f" [{label}: {(value or name).strip()}] ")
        elif tag == "textarea":
            add(f" [поле текст: {(el.get('placeholder') or el.get('name') or '').strip()}] ")
        elif tag == "select":
            opts = [" ".join((o.text_content() or "").split()) for o in el.findall(".//option")]
            add(f" [список {el.get('name') or ''}: {', '.join(o for o in opts if o)[:300]}] ")
        elif tag == "button":
            add(f" [кнопка: {' '.join((el.text_content() or '').split())}] ")
        elif tag == "img" and el.get("alt"):
            add(f" [картинка: {el.get('alt').strip()}] ")
        else:
            if el.text:
                add(el.text)
            for child in el:
                walk(child)
            if tag == "a" and el.get("href") and not el.get("href", "").startswith(("#", "javascript:")):
                add(f" ({urljoin(base, el.get('href'))})")
        if tag in BLOCKS:
            add("\n")
        if el.tail:
            add(el.tail)

    body = doc.find("body")
    walk(body if body is not None else doc)
    text = "".join(out)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n(?: ?\| ?)+", "\n| ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return title, text


def fetch(url: str, http=None, tesseract: str | None = None) -> tuple[str, str]:
    """Скачать страницу: (название материала, текст). Ошибки — LinkError с причиной по-русски."""
    import httpx

    url = check_url(url)
    client = http or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(TIMEOUT, connect=10),
                                  headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"})
    try:
        with client.stream("GET", url) as r:
            check_url(str(r.url))  # переадресация на локальный адрес — так же нельзя
            if r.status_code in (401, 403, 429):
                raise LinkError(f"сайт не отдал страницу программе (код {r.status_code}): открой её в браузере, "
                                "сохрани как PDF (Ctrl+P → «Сохранить как PDF») и добавь файлом")
            if r.status_code == 404:
                raise LinkError("такой страницы нет (код 404) — проверь ссылку")
            if r.status_code >= 400:
                raise LinkError(f"сайт ответил ошибкой (код {r.status_code}) — попробуй позже")
            data = b""
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > MAX_BYTES:
                    raise LinkError("страница слишком большая (больше 4 МБ) — сохрани нужную часть в PDF и добавь "
                                    "файлом")
            kind = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            final = str(r.url)
            encoding = r.encoding or "utf-8"
    except LinkError:
        raise
    except httpx.TimeoutException:
        raise LinkError("сайт не ответил за 25 секунд — проверь интернет или попробуй позже") from None
    except httpx.HTTPError as e:
        from forlabs.core.netcert import is_cert_error

        if is_cert_error(e):
            raise LinkError("сертификат сайта не прошёл проверку — открой его в браузере и сохрани как PDF") from None
        raise LinkError("не удалось связаться с сайтом — проверь интернет и ссылку") from None
    finally:
        if http is None:
            client.close()
    name_tail = Path(urlsplit(final).path).name
    ext = Path(name_tail).suffix.lower()
    if kind in ("application/pdf",) or (ext in FILE_EXT and "html" not in kind):
        return _as_file(name_tail or "страница", data, ext or ".pdf", tesseract, final)
    if "html" not in kind and not kind.startswith("text/") and kind not in ("application/json", "application/xml"):
        raise LinkError(f"по ссылке не страница, а файл «{kind or 'неизвестный'}» — скачай его и добавь файлом")
    raw = data.decode(encoding, errors="replace")
    if "html" in kind or raw.lstrip()[:200].lower().startswith(("<!doctype html", "<html")):
        title, text = html_text(raw, final)
    else:
        title, text = "", raw
    if len(text) < 200:
        raise LinkError("на странице почти нет текста без браузера (сайт собирает её скриптами): открой её, "
                        "сохрани как PDF и добавь файлом")
    host = urlsplit(final).hostname or ""
    name = (title or host)[:80]
    return f"{name} — {host}", f"Страница: {final}\n\n{text}"


def _as_file(name: str, data: bytes, ext: str, tesseract: str | None, url: str) -> tuple[str, str]:
    import tempfile

    from forlabs.pipeline.materials import read_material

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / ("файл" + ext)
        path.write_bytes(data)
        try:
            text = read_material(path, tesseract)
        except ValueError as e:
            raise LinkError(str(e)) from None
    return f"{name} — {urlsplit(url).hostname}", f"Файл по ссылке: {url}\n\n{text}"
