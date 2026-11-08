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


def main() -> None:
    import uvicorn

    from forlabs.core.paths import ensure_dirs
    from forlabs.ui.app import create_app

    ensure_dirs()
    port = _free_port(int(os.environ.get("FORLABS_PORT", "8765")))
    url = f"http://127.0.0.1:{port}/"
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    no_window = "--browser" in sys.argv or os.environ.get("FORLABS_BROWSER_UI") == "1"
    try:
        if no_window:
            raise ImportError
        import webview  # pywebview

        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        webview.create_window("ForLabs", url, width=1400, height=900, min_size=(900, 600))
        webview.start()
        server.should_exit = True
    except ImportError:
        print(f"ForLabs открыт в браузере: {url}  (закрыть — Ctrl+C)")
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        server.run()


if __name__ == "__main__":
    main()
