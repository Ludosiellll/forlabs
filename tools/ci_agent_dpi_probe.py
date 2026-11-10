"""Windows (CI): ИИ в чужих программах при масштабе экрана 125 %, как на ноутбуках.

Все проверки «рук и глаз» агента (tools/ci_agent_probe.py: Блокнот, Visual Studio, IDLE, Paint, чужие окна, Esc)
плюс программы, которые сами масштаб не поддерживают и Windows их растягивает: WinForms и Qt 5 (на Qt сделан Cisco
Packet Tracer) в двух режимах. Для каждой: снимок окна целиком, без чёрных полей, и щелчок по кнопке попадает в
кнопку. Отчёт: build/probe/agent/report.json, снимки рядом."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from ci_dpi_probe import black_edges, get_scale, set_scale  # noqa: E402

QT_APP = '''import sys
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

app = QApplication(sys.argv)
w = QWidget()
w.setWindowTitle("Топология сети")
box = QVBoxLayout(w)
label = QLabel("Пакет не отправлен")
button = QPushButton("Отправить пакет")
button.setMinimumHeight(60)
button.clicked.connect(lambda: label.setText("Пакет доставлен: PC0 -> Server0"))
box.addWidget(label)
box.addWidget(button)
w.resize(420, 220)
w.show()
sys.exit(app.exec_())
'''


def main() -> int:
    report_scale = {}
    try:
        set_scale(125)
        report_scale = get_scale()
    except Exception as e:  # noqa: BLE001
        report_scale = {"error": f"{type(e).__name__}: {e}"}
    print("масштаб:", report_scale, flush=True)
    try:
        import ci_agent_probe as p  # проверки идут сразу при загрузке — уже при 125 %
        from ci_window_probe import DESIGNER_GOOD, build, plan

        from forlabs.agent.core import Action
        from forlabs.agent.windesk import WinDesk
        from forlabs.runner import languages

        p.REPORT["scale"] = report_scale

        def stretched(res, name: str, exe: str, files: list[Path], button: str, changed) -> None:
            """Программа, которую Windows растягивает: снимок агента — окно целиком, щелчок по кнопке попадает в неё."""
            folder = p.WORK / name
            folder.mkdir(parents=True, exist_ok=True)
            desk = WinDesk("notepad", exe, files, folder, launch_timeout=60)
            try:
                time.sleep(3)
                obs = desk.observe()
                obs.image.save(p.OUT / f"dpi_{name}_before.png")
                right, bottom = black_edges(obs.image)
                res.update(size=list(obs.image.size), black_right=right, black_bottom=bottom,
                           elements=p.elements_dump(obs, 15))
                assert right < 0.2 and bottom < 0.2, "на снимке чёрные поля"
                el = next((e for e in obs.elements if button.lower() in e.name.lower()), None)
                assert el is not None, f"кнопки «{button}» нет среди элементов"
                desk.act(Action(do="click", el=el.id), obs)
                time.sleep(1.5)
                after = desk.observe()
                after.image.save(p.OUT / f"dpi_{name}_after.png")
                res["after_title"], res["after_elements"] = after.title, p.elements_dump(after, 15)
                assert changed(obs, after), "щелчок не попал в кнопку"
            finally:
                p.kill_tree(desk.root)

        def differs(a, b, box) -> bool:
            from PIL import ImageChops

            w, h = a.image.size
            region = tuple(int(v) for v in (box[0] * w, box[1] * h, box[2] * w, box[3] * h))
            if a.image.size != b.image.size:
                return True
            diff = ImageChops.difference(a.image.crop(region).convert("L"), b.image.crop(region).convert("L"))
            return sum(1 for v in diff.getdata() if v > 40) > 50

        tools = languages.detect()
        winforms = build("agent_dpi_winforms", plan(DESIGNER_GOOD, []), tools)

        @p.case
        def winforms_stretched_window_whole_and_clickable(res):
            # пустые поля → окно «Ошибка» с «Введите целые числа!»: значит, щелчок попал в «Вычислить»
            stretched(res, "winforms", winforms.cmd[0], [], "Вычислить",
                      lambda before, after: any("целые числа" in (e.name or "") for e in after.elements)
                      or "Ошибка" in (after.title or "") or differs(before, after, (0, 0, 1, 1)))

        qt_ok = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "PyQt5==5.15.11"],
                               capture_output=True).returncode == 0
        script = p.WORK / "qt_app.py"
        script.write_text(QT_APP, encoding="utf-8")
        for mode, title in (("0", "unaware"), ("1", "system")):
            def qt_case(res, mode=mode, title=title):
                if not qt_ok:
                    res.update(ok=False, error="PyQt5 не установился")
                    return
                os.environ["QT_QPA_PLATFORM"] = f"windows:dpiawareness={mode}"  # Qt 5 без поддержки масштаба
                try:
                    stretched(res, f"qt_{title}", sys.executable, [script], "Отправить",
                              lambda before, after: any("доставлен" in (e.name or "") for e in after.elements)
                              or differs(before, after, (0, 0, 1, 0.45)))
                finally:
                    os.environ.pop("QT_QPA_PLATFORM", None)

            qt_case.__name__ = f"qt5_{title}_window_whole_and_clickable"
            p.case(qt_case)
        rc = p.main()
    finally:
        try:
            set_scale(100)
        except Exception:  # noqa: BLE001
            pass
    return rc


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
