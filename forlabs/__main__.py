"""Запуск: python -m forlabs  (окно приложения через pywebview, иначе — браузер)."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser


def _free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def already_running(port: int) -> bool:
    """На порту уже работает ForLabs (второй запуск открывает его окно, а не второй сервер)."""
    import httpx

    try:
        r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.5, trust_env=False)
        return r.status_code == 200 and r.json().get("app") == "forlabs"
    except Exception:
        return False


def build_server(port: int, ctx=None):
    import uvicorn

    from forlabs.core.logs import setup_logging
    from forlabs.ui.app import create_app

    setup_logging()  # до uvicorn: под pythonw нет консоли
    config = uvicorn.Config(create_app(ctx), host="127.0.0.1", port=port, log_level="warning",
                            log_config=None, use_colors=False)
    return uvicorn.Server(config)


def _open_window(url: str, ctx=None) -> None:
    import webview  # pywebview

    window = webview.create_window("ForLabs", url, width=1400, height=900, min_size=(900, 600))

    def on_closing():  # закрытие во время работы: спросить, иначе работа оборвётся
        try:
            if ctx is not None and ctx.running:
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
    webview.start()


def main() -> None:
    from forlabs.core.logs import setup_logging
    from forlabs.core.paths import ensure_dirs
    from forlabs.ui.context import AppContext

    setup_logging()
    ensure_dirs()
    preferred = int(os.environ.get("FORLABS_PORT", "8765"))
    no_window = "--browser" in sys.argv or os.environ.get("FORLABS_BROWSER_UI") == "1"
    if already_running(preferred):  # второй запуск: тот же сервер, те же данные
        url = f"http://127.0.0.1:{preferred}/"
        if no_window:
            webbrowser.open(url)
            return
        try:
            _open_window(url)
        except ImportError:
            webbrowser.open(url)
        return
    port = _free_port(preferred)
    url = f"http://127.0.0.1:{port}/"
    ctx = AppContext()
    server = build_server(port, ctx)
    try:
        if no_window:
            raise ImportError
        import webview  # noqa: F401 — pywebview

        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        _open_window(url, ctx)
        server.should_exit = True
    except ImportError:
        print(f"ForLabs открыт в браузере: {url}  (закрыть — Ctrl+C)")
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        server.run()


if __name__ == "__main__":
    main()
