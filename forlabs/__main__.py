"""Запуск: python -m forlabs  (окно приложения через pywebview, иначе — браузер)."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser


def _bind(preferred: int) -> socket.socket:
    """Сокет сервера: нужный порт, иначе любой свободный. Привязываем сами — порт, «свободный» для connect, но
    закрытый Windows (Hyper-V/WSL), иначе тихо уронил бы сервер, а окно открылось бы на пустой адрес."""
    for port in (preferred, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            s.listen(64)
            return s
        except OSError:
            s.close()
    raise OSError("Не удалось открыть порт для ForLabs на 127.0.0.1")


def already_running(port: int) -> bool:
    """На порту отвечает ForLabs (проверка /health)."""
    import httpx

    try:
        r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.5, trust_env=False)
        return r.status_code == 200 and r.json().get("app") == "forlabs"
    except Exception:
        return False


def contact_primary(timeout: float = 20.0) -> int | None:
    """Второй запуск: найти свой (этого пользователя) работающий ForLabs по instance.json и попросить вывести окно
    вперёд. Возвращает его порт или None."""
    import httpx

    from forlabs.core.instance import read_instance

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        inst = read_instance()
        if inst:
            port, token = inst
            try:
                r = httpx.post(f"http://127.0.0.1:{port}/focus", headers={"X-ForLabs-Token": token}, timeout=3,
                               trust_env=False)
                if r.status_code == 200:
                    return port
            except Exception:
                pass
        time.sleep(0.5)  # первый ещё запускается
    return None


def build_server(port: int, ctx=None, token: str | None = None):
    import uvicorn

    from forlabs.core.logs import setup_logging
    from forlabs.ui.app import create_app

    setup_logging()  # до uvicorn: под pythonw нет консоли
    config = uvicorn.Config(create_app(ctx, token=token), host="127.0.0.1", port=port, log_level="warning",
                            log_config=None, use_colors=False, http="h11", ws="none", loop="asyncio")
    return uvicorn.Server(config)


def _open_window(url: str, ctx=None) -> None:
    import webview  # pywebview

    if hasattr(webview, "settings"):
        webview.settings["ALLOW_DOWNLOADS"] = True  # иначе «Скачать .docx» в окне молча отменяется
    window = webview.create_window("ForLabs", url, width=1280, height=800, min_size=(900, 600), maximized=True)

    def on_closing():  # закрытие во время работы: спросить, иначе работа оборвётся
        try:
            if ctx is not None and ctx.busy():
                return window.create_confirmation_dialog(
                    "ForLabs ещё работает",
                    "Сейчас выполняется лаба. Если закрыть окно, работа прервётся — потом её можно будет "
                    "продолжить кнопкой «Продолжить». Закрыть?")
        except Exception:
            return True
        return True

    try:
        window.events.closing += on_closing
    except Exception:
        pass
    if ctx is not None:
        ctx.quit = window.destroy  # «Обновить»: окно закрывается, установщик ставит новую версию и открывает её

        def focus():  # второй запуск ForLabs: вывести это окно вперёд
            try:
                window.restore()
                window.show()
                window.on_top = True
                window.on_top = False
            except Exception:
                pass

        ctx.focus = focus
    # на Windows только WebView2: без него pywebview тихо взял бы движок IE (без fetch и потоков событий) —
    # лучше ошибка, тогда откроется обычный браузер
    webview.start(gui="edgechromium") if sys.platform == "win32" else webview.start()


def open_ui(url: str, ctx=None) -> str:
    """Окно приложения; не открылось (нет WebView2, ошибка .NET, старая система) — обычный браузер, а не тихий
    выход без единого сообщения."""
    import logging

    try:
        _open_window(url, ctx)
        return "window"
    except Exception as e:
        logging.getLogger("forlabs").warning("Окно не открылось (%s) — открываю браузер", e, exc_info=e)
        webbrowser.open(url)
        return "browser"


def _wait_forever(server) -> None:
    try:
        while not server.should_exit:
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.should_exit = True


def main() -> None:
    import secrets

    from forlabs.core.instance import InstanceLock, clear_instance, write_instance
    from forlabs.core.logs import setup_logging
    from forlabs.core.netcert import use_system_certificates
    from forlabs.core.paths import ensure_dirs
    from forlabs.ui.context import AppContext

    from forlabs.core.settings import use_windows_vault

    setup_logging()
    use_system_certificates()  # сеть колледжа или антивирус с проверкой HTTPS
    use_windows_vault()
    ensure_dirs()
    no_window = "--browser" in sys.argv or os.environ.get("FORLABS_BROWSER_UI") == "1"
    lock = InstanceLock()
    if not lock.acquire():  # уже запущен (или запускается) у этого пользователя: вывести его окно вперёд
        port = contact_primary()
        if port is None:
            print("ForLabs уже запускается — подожди несколько секунд.")
        return
    try:
        from forlabs.render.word_com import cleanup_stale_word

        cleanup_stale_word()  # Word, брошенный при прошлом закрытии во время доводки
    except Exception:
        pass
    token = secrets.token_urlsafe(24)
    sock = _bind(int(os.environ.get("FORLABS_PORT", "8765")))
    port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}/?t={token}"
    ctx = AppContext()
    server = build_server(port, ctx, token=token)
    try:
        threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True).start()
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("Сервер ForLabs не запустился — подробности в forlabs.log")
        write_instance(port, token)
        ctx.updater.check_in_background()  # есть ли новая версия — в фоне, карточка в углу появится сама
        ctx.quit = lambda: setattr(server, "should_exit", True)  # без окна; окно заменит на своё закрытие
        if no_window:
            ctx.focus = lambda: webbrowser.open(url)
            print(f"ForLabs открыт в браузере: http://127.0.0.1:{port}/  (закрыть — Ctrl+C)")
            webbrowser.open(url)
            _wait_forever(server)
        elif open_ui(url, ctx) == "browser":
            ctx.focus = lambda: webbrowser.open(url)
            _wait_forever(server)
    finally:
        server.should_exit = True
        clear_instance()
        lock.release()


if __name__ == "__main__":
    main()
