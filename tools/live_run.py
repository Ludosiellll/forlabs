"""Живой прогон конвейера на реальной методичке (нужен FORLABS_API_KEY; данные — в FORLABS_HOME).

    python tools/live_run.py МЕТОДИЧКА.txt ПРЕДМЕТ [m|f] [ВАРИАНТ]

Скриншоты-заглушки создаются автоматически (серые картинки с подписью), чтобы проверить вёрстку целиком.
"""

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forlabs.core.llm import LLM  # noqa: E402
from forlabs.core.models import Enrollment, Profile  # noqa: E402
from forlabs.core.settings import Settings  # noqa: E402
from forlabs.core.store import Store  # noqa: E402
from forlabs.core.subjects import get_subject  # noqa: E402
from forlabs.pipeline.memory import SubjectMemory  # noqa: E402
from forlabs.pipeline.run import Pipeline  # noqa: E402
from forlabs.render.conformance import compare  # noqa: E402


def fake_shot(text: str) -> bytes:
    im = Image.new("RGB", (1280, 720), (243, 243, 243))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1279, 34], fill=(225, 225, 225))
    d.text((16, 60), text[:120], fill=(40, 40, 40))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def main() -> None:
    src, subject_id = Path(sys.argv[1]), sys.argv[2]
    gender = sys.argv[3] if len(sys.argv) > 3 else "m"
    subject = get_subject(subject_id)
    variant = int(sys.argv[4]) if len(sys.argv) > 4 else subject.default_variant
    workplace = int(sys.argv[5]) if len(sys.argv) > 5 else None
    settings = Settings(mode="local", base_url=os.environ.get("FORLABS_BASE_URL", Settings().base_url),
                        model=os.environ.get("FORLABS_MODEL", Settings().model))
    store = Store()
    pid = "demo_" + gender
    store.save_profile(Profile(
        id=pid, full_name="Иванова Анна Петровна" if gender == "f" else "Иванов Иван Петрович",
        title_name="А.П. Иванова" if gender == "f" else "И.П. Иванов", gender=gender, group="РС02-23",
        doc_author="Иванова Анна" if gender == "f" else "Иванов Иван",
        output_root=str(Path(os.environ.get("FORLABS_HOME", ".")) / "сдача"),
        enrollments=[Enrollment(subject_id=subject_id, title_subject=subject.name, teacher="Т.Т. Тестова",
                                academic_year="2026/2027", variant=variant, workplace=workplace,
                                header_style=subject.default_header_style)]))
    llm = LLM(settings, api_key=os.environ["FORLABS_API_KEY"])
    pipe = Pipeline(store, llm_factory=lambda: llm, prefer_word=False, memory=SubjectMemory())
    pipe.bus.publish = (lambda orig: (lambda key, text, kind="info", step=None:
                                      (print(f"  [{kind}] {text}"), orig(key, text, kind, step))[1]))(pipe.bus.publish)
    t0 = time.time()
    text = src.read_text(encoding="utf-8").replace("<USER_REQUEST>", "").replace("</USER_REQUEST>", "")
    st = pipe.create_lab(pid, subject_id, [("методичка.txt", text.encode("utf-8"))])
    st = pipe.parse(st)
    if st.status == "failed":
        sys.exit(f"разбор: {st.error}")
    print(f"РАЗБОР ({time.time() - t0:.0f} с): ЛР {st.spec.number} «{st.spec.topic}»; вариант {st.spec.variant}")
    for t in st.spec.tasks:
        print(f"   {t.n}. [{t.kind}] {t.text[:110]}")
    print("   вариант:", (st.spec.variant_text or "—")[:200])
    print("   вопросов:", len(st.spec.questions), "| замечания разбора:", [f.location for f in st.findings])
    st = pipe.execute(pipe.confirm(st, None))
    if st.status == "failed":
        sys.exit(f"выполнение: {st.error}")
    for sec_n, blk, fig in pipe.slots(st):
        st = pipe.attach_screenshot(st, sec_n, blk, fake_shot(f"Снимок: {fig.topic}"))
    print(f"ГОТОВО ({time.time() - t0:.0f} с): статус {st.status}, страниц {len(st.pages)}, токенов {llm.usage.total_tokens}")
    print("   замечания:", [(f.rule_id, f.location) for f in st.findings])
    print("   сверка с каноном:", compare(st.docx_path) or "0 расхождений")
    print("   docx:", st.docx_path)
    print("   pdf:", st.pdf_path)


if __name__ == "__main__":
    main()
