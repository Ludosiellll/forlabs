"""Проверка сборки и окружения.

    python -m forlabs.selfcheck                       # из исходников
    ForLabs.exe --selfcheck [--out файл] [--offline]  # собранная программа (у exe без консоли вывод — в файл)

Код выхода 1, если сломана сама программа (правила, словари, шаблоны, документ, сервер). Word, LibreOffice,
Tesseract и облако только показываются: без них ForLabs работает, просто беднее.
"""

from __future__ import annotations

import importlib
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Check:
    name: str
    ok: bool | None
    detail: str


def _line(ok: bool | None, name: str, detail: str) -> str:
    mark = {True: "есть ", False: "НЕТ  ", None: "—    "}[ok]
    return f"[{mark}] {name}: {detail}"


def _run(name: str, fn: Callable[[], str]) -> Check:
    try:
        return Check(name, True, fn())
    except Exception as e:  # самопроверка не падает: каждая поломка — строка отчёта
        return Check(name, False, (f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)[:300])


def _version() -> str:
    from forlabs import version

    kind = "собранная программа" if version.frozen() else "из исходников"
    return f"{version.__version__} ({kind}, Python {sys.version.split()[0]})"


def _rules() -> str:
    from forlabs import version
    from forlabs.core import rules

    files = rules.builtin_rules()
    if not files:
        raise RuntimeError("нет ни одного файла правил")
    if version.frozen() and rules.rules_source() != "embedded":
        raise RuntimeError("не встроены в программу — сборка без tools/embed_rules.py")
    missing = [p for p in rules.STAGE_FILES["write"] if p not in files]
    if missing:
        raise RuntimeError("нет: " + ", ".join(missing))
    where = "встроены в программу" if rules.rules_source() == "embedded" else "из папки пакета"
    return f"{len(files)} файлов, {where}"


def _morph() -> str:
    from forlabs.report.morph import is_known_word, past_tense

    if not is_known_word("студент"):
        raise RuntimeError("словарь не знает слово «студент»")
    got = past_tense("выполнить", "f")
    if got != "выполнила":
        raise RuntimeError(f"«выполнить» → «{got}»")
    return "словари русского языка на месте"


def _libraries() -> str:
    names = ["docx", "lxml", "pymorphy3", "pymupdf", "fastapi", "uvicorn", "httpx", "jinja2", "python_multipart",
             "pydantic", "PIL", "truststore", "keyring", "playwright"]
    if sys.platform == "win32":
        names += ["webview", "win32com.client", "pythoncom"]
    missing = []
    for n in names:
        try:
            importlib.import_module(n)
        except Exception as e:
            missing.append(f"{n} ({type(e).__name__})")
    if missing:
        raise RuntimeError("не загружаются: " + ", ".join(missing))
    return f"{len(names)} загружаются"


def _assets() -> str:
    from forlabs.executors.diagrams import VIZ_JS
    from forlabs.render.canon import ASSETS
    from forlabs.ui.app import HERE

    need = [HERE / "templates" / "base.html", HERE / "templates" / "lab.html", HERE / "static" / "app.css",
            HERE / "static" / "app.js", ASSETS / "base.docx", ASSETS / "canon.json", ASSETS / "title.xml", VIZ_JS]
    missing = [p.name for p in need if not p.is_file()]
    if missing:
        raise RuntimeError("нет файлов: " + ", ".join(missing))
    fonts = list((HERE / "static" / "fonts").glob("*.woff2"))
    if not fonts:
        raise RuntimeError("нет шрифтов интерфейса")
    return f"шаблоны, оформление колледжа, {len(fonts)} шрифтов"


def _document() -> str:
    import docx

    from forlabs.core.models import Paragraph, ReportDoc, Table, TaskSection
    from forlabs.render.docx_builder import build_lab

    doc = ReportDoc(lab_number="1", header={"Тема": "Проверка"}, conclusion="Проверка выполнена.",
                    sections=[TaskSection(n=1, title="Проверка", blocks=[
                        Paragraph(text="Текст абзаца."), Table(topic="проверки", rows=[["А", "Б"], ["1", "2"]])])])
    with tempfile.TemporaryDirectory() as d:
        out = build_lab(doc, Path(d) / "проверка.docx", author="ForLabs")
        text = "\n".join(p.text for p in docx.Document(str(out)).paragraphs)
        if "Текст абзаца." not in text:
            raise RuntimeError("в документе нет текста")
        return f"docx собирается ({out.stat().st_size // 1024} КБ)"


def _pdf() -> str:
    import pymupdf

    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "ForLabs")
    data = pdf.tobytes()
    if not pymupdf.open(stream=data, filetype="pdf")[0].get_text().strip():
        raise RuntimeError("PDF не читается")
    return f"PyMuPDF {pymupdf.VersionBind}"


def _server() -> str:
    import socket
    import threading
    import time

    import httpx

    from forlabs import version
    from forlabs.__main__ import build_server

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = build_server(port, None, token="selfcheck")
    t = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    t.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("не запустился за 10 секунд")
        data = httpx.get(f"http://127.0.0.1:{port}/health", timeout=10, trust_env=False).json()
        if data.get("version") != version.__version__:
            raise RuntimeError(f"ответил не то: {data}")
        return f"отвечает на 127.0.0.1:{port}"
    finally:
        server.should_exit = True
        t.join(10)
        sock.close()


def _vault() -> str:
    import keyring

    from forlabs.core.settings import use_windows_vault

    use_windows_vault()
    name = type(keyring.get_keyring()).__name__
    if name != "WinVaultKeyring":
        raise RuntimeError(f"хранилище {name} вместо «Диспетчера учётных данных»")
    keyring.set_password("ForLabs-selfcheck", "probe", "ok")
    try:
        if keyring.get_password("ForLabs-selfcheck", "probe") != "ok":
            raise RuntimeError("запись не читается обратно")
    finally:
        keyring.delete_password("ForLabs-selfcheck", "probe")
    return "«Диспетчер учётных данных» Windows"


def build_checks() -> list[Check]:
    """Сама программа: всё это обязано работать в собранном exe."""
    checks = [_run("Версия", _version), _run("Правила", _rules), _run("Морфология", _morph),
              _run("Библиотеки", _libraries), _run("Шаблоны и оформление", _assets), _run("Документ", _document),
              _run("PDF", _pdf), _run("Сервер", _server)]
    if sys.platform == "win32":
        checks.append(_run("Хранилище паролей", _vault))
    return checks


def environment_lines(offline: bool = False) -> list[str]:
    """Окружение компьютера: только показываем."""
    from forlabs.core.settings import get_api_key, load_settings
    from forlabs.render import libreoffice, word_com

    s = load_settings()
    lines = [_line(word_com.available(), "Microsoft Word",
                   "доводка документа и «Продолжение листинга» через Word" if word_com.available()
                   else "не найден — номера страниц посчитает LibreOffice")]
    lo = libreoffice.find_soffice(s.soffice_path)
    lines.append(_line(bool(lo) or None, "LibreOffice", lo or "не обязателен при наличии Word"))
    from forlabs.executors.diagrams import browser_available

    lines.append(_line(browser_available(), "Браузер для схем", "Edge/Chromium найден" if browser_available()
                       else "не найден — схемы придётся вставлять картинкой"))
    from forlabs.ingest.extract import find_tesseract

    tess = find_tesseract(s.tesseract_path)
    lines.append(_line(bool(tess) or None, "Tesseract", tess or "нужен только для фото методичек"))
    lines.append(_line(True, "Режим", "облако (вход по e-mail)" if s.mode == "cloud" else "локальный"))
    if s.mode == "local":
        lines.append(_line(bool(get_api_key()), "Ключ API", "сохранён" if get_api_key() else "не задан — Настройки"))
    elif not offline:
        import httpx

        from forlabs.cloud.config import DEFAULT

        try:
            r = httpx.get(f"{DEFAULT.url}/auth/v1/health", headers={"apikey": DEFAULT.publishable_key}, timeout=10)
            lines.append(_line(r.status_code == 200, "Облако Supabase", f"ответ {r.status_code}"))
        except httpx.HTTPError as e:
            lines.append(_line(False, "Облако Supabase", f"нет связи: {e}"))
    return lines


def main(argv: list[str] | None = None) -> int:
    from forlabs.core.netcert import use_system_certificates

    argv = sys.argv[1:] if argv is None else argv
    out = argv[argv.index("--out") + 1] if "--out" in argv and argv.index("--out") + 1 < len(argv) else None
    use_system_certificates()  # как в приложении: сеть колледжа и антивирус с проверкой HTTPS
    checks = build_checks()
    lines = [_line(c.ok, c.name, c.detail) for c in checks]
    try:
        lines += environment_lines(offline="--offline" in argv)
    except Exception as e:
        lines.append(_line(None, "Окружение", f"не удалось проверить: {e}"))
    failed = [c.name for c in checks if not c.ok]
    lines.append("ИТОГ: " + ("всё в порядке" if not failed else "сломано: " + ", ".join(failed)))
    text = "\n".join(lines)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
    if sys.stdout is not None:
        try:
            print(text)
        except (OSError, UnicodeEncodeError):
            pass
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
