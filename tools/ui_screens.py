"""Скриншоты всех экранов для визуальной проверки (локальный режим, данные из FORLABS_HOME).

    python tools/ui_screens.py ПАПКА_ДЛЯ_PNG
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from forlabs.core.settings import Settings  # noqa: E402
from forlabs.core.store import Store  # noqa: E402
from forlabs.executors.diagrams import FALLBACK_EXECUTABLES  # noqa: E402
from forlabs.ui.app import create_app  # noqa: E402
from forlabs.ui.context import AppContext  # noqa: E402


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ctx = AppContext(settings=Settings(mode="local"), prefer_word=False)
    app = create_app(ctx)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8799, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    store = Store()
    pages = [("home", "/"), ("profiles", "/profiles"), ("profile_new", "/profiles/new"),
             ("lab_new", "/labs/new"), ("semester", "/semester"), ("rules", "/rules"), ("settings", "/settings")]
    for p in store.list_profiles():
        pages.append(("profile_edit", f"/profiles/{p.id}"))
        for e in p.enrollments:
            for lab in store.list_labs(p.id, e.subject_id):
                base = f"/labs/{p.id}/{e.subject_id}/{lab.number}"
                pages += [(f"lab_{lab.number}_report", base + "?tab=report"), (f"lab_{lab.number}_spec", base + "?tab=spec"),
                          (f"lab_{lab.number}_journal", base + "?tab=journal")]
        break
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        exe = next((x for x in FALLBACK_EXECUTABLES if Path(x).exists()), None)
        browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        for width, height, tag in ((1366, 860, "desk"), (390, 844, "phone")):
            page = browser.new_page(viewport={"width": width, "height": height})
            for name, url in pages:
                page.goto(f"http://127.0.0.1:8799{url}")
                page.wait_for_load_state("networkidle")
                page.screenshot(path=str(out / f"{tag}_{name}.png"), full_page=tag == "desk" and "lab_" not in name)
            page.close()
        browser.close()
    server.should_exit = True
    print("готово:", out)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
