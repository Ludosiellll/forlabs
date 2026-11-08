"""Проверка окружения: python -m forlabs.selfcheck"""

from __future__ import annotations

import importlib
import shutil
import sys


def _line(ok: bool | None, name: str, detail: str) -> str:
    mark = {True: "есть ", False: "НЕТ  ", None: "—    "}[ok]
    return f"[{mark}] {name}: {detail}"


def main() -> int:
    from forlabs.core.settings import get_api_key, load_settings
    from forlabs.render import libreoffice, word_com

    s = load_settings()
    lines = [_line(sys.version_info >= (3, 11), "Python", sys.version.split()[0])]
    missing = [m for m in ("docx", "lxml", "pymorphy3", "pymupdf", "fastapi", "uvicorn", "httpx", "playwright", "PIL")
               if importlib.util.find_spec(m) is None]
    lines.append(_line(not missing, "Библиотеки", "все на месте" if not missing else "нет: " + ", ".join(missing)))
    lines.append(_line(word_com.available(), "Microsoft Word",
                       "доводка документа и «Продолжение листинга» через Word" if word_com.available()
                       else "не найден — номера страниц посчитает LibreOffice"))
    lo = libreoffice.find_soffice(s.soffice_path)
    lines.append(_line(bool(lo) or None, "LibreOffice", lo or "не обязателен при наличии Word"))
    from forlabs.executors.diagrams import browser_available

    lines.append(_line(browser_available(), "Браузер для схем", "Edge/Chromium найден" if browser_available()
                       else "не найден — схемы придётся вставлять картинкой"))
    tess = s.tesseract_path or shutil.which("tesseract")
    lines.append(_line(bool(tess) or None, "Tesseract", tess or "нужен только для фото методичек"))
    lines.append(_line(True, "Режим", "облако (вход по e-mail)" if s.mode == "cloud" else "локальный"))
    if s.mode == "local":
        lines.append(_line(bool(get_api_key()), "Ключ API", "сохранён" if get_api_key() else "не задан — Настройки"))
    else:
        import httpx

        from forlabs.cloud.config import DEFAULT

        try:
            r = httpx.get(f"{DEFAULT.url}/auth/v1/health", headers={"apikey": DEFAULT.publishable_key}, timeout=10)
            lines.append(_line(r.status_code == 200, "Облако Supabase", f"ответ {r.status_code}"))
        except httpx.HTTPError as e:
            lines.append(_line(False, "Облако Supabase", f"нет связи: {e}"))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
