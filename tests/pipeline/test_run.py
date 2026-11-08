import io
import zipfile
from pathlib import Path

from PIL import Image

from forlabs.core.llm import FakeLLM, LLMError
from forlabs.core.models import Enrollment, Profile
from forlabs.core.rules import RuleBook
from forlabs.core.store import Store
from forlabs.pipeline.events import EventBus
from forlabs.pipeline.run import Pipeline
from forlabs.render.conformance import compare

FIXTURE = Path(__file__).parents[1] / "fixtures" / "methodology_psip_lr7.txt"
TASKS = ["Создайте 2 простых анимации к выбранной тематике.", "Создайте анимацию с эффектом параллакса."]
RAW = {"number": "7", "topic": "Создание визуальных эффектов и анимации на HTML странице",
       "goal": "Сформировать умение создавать визуальные эффекты и анимации на HTML – странице",
       "equipment": "Методические указания к выполнению, ОС Windows, Sublime Text, MS Word",
       "assignment": "Задания выполняются к своей теме из ЛР №2", "main_tasks": TASKS, "extra_tasks": [],
       "variant_text": None, "questions": ["Hover-эффекты и их применение."]}
SEC = {"blocks": [
    {"type": "paragraph", "text": "В файле style.css описаны две анимации для страницы о Бобруйске:"},
    {"type": "dash_list", "items": ["- плавное появление заголовка;", "- покачивание герба."]},
    {"type": "listing", "topic": "Исходный код анимаций в файле style.css", "language": "css",
     "code": "@keyframes fade {\n  from { opacity: 0; }\n  to { opacity: 1; }\n}"},
    {"type": "figure", "kind": "screenshot", "topic": "Анимация заголовка в окне браузера",
     "instruction": "Открой index.html в браузере"},
]}
SEC2 = {"blocks": [{"type": "paragraph", "text": "Фон шапки движется медленнее текста при прокрутке."}]}
ANS = {"answers": ["Смена вида элемента при наведении курсора."]}


def setup(tmp_home, tmp_path, responses):
    store = Store()
    p = Profile(id="ivanov", full_name="Иванов Иван Петрович", title_name="И.П. Иванов", gender="m",
                output_root=str(tmp_path / "сдача"),
                enrollments=[Enrollment(subject_id="psip", title_subject="ПССИП", teacher="Т.Т. Тестова",
                                        academic_year="2026/2027", variant=14, header_style="short")])
    store.save_profile(p)
    fake = FakeLLM(responses)
    pipe = Pipeline(store, llm_factory=lambda: fake, rules=RuleBook(), bus=EventBus(), render_diagrams=False,
                    prefer_word=False)
    return pipe, fake


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (900, 500), (240, 240, 240)).save(buf, format="PNG")
    return buf.getvalue()


def test_full_pipeline_fake_llm(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number=None)
    assert st.status == "draft"
    st = pipe.parse(st, date="24.09.2026")
    assert st.status == "parsed" and st.number == "7" and len(st.spec.tasks) == 2
    st = pipe.confirm(st, st.spec)
    st = pipe.execute(st)
    assert st.status == "needs_manual", st.error
    assert st.docx_path and Path(st.docx_path).exists()
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    assert st.status == "rendered", st.error
    assert compare(st.docx_path) == []
    assert st.pages and Path(st.pages[0]).exists()
    st = pipe.mark_final(st)
    assert st.status == "final"
    out = Path(tmp_path / "сдача" / "ПССИП" / "ЛР7" / "ЛР7_ПССИП.docx")
    assert out.exists()
    xml = zipfile.ZipFile(out).read("word/document.xml").decode()
    assert "Вставить скриншот" not in xml and "Сформировал умение" in xml


def test_llm_failure_keeps_state(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, LLMError("network", "нет сети")])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.confirm(pipe.parse(st, date="24.09.2026"), None)
    st = pipe.execute(st)
    assert st.status == "failed" and "связи" in st.error
    reloaded = pipe.store.load_lab("ivanov", "psip", "7")
    assert reloaded.spec is not None and reloaded.status == "failed"


def test_redo_single_task(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS, SEC2])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    before = st.report.sections[0].model_dump()
    st = pipe.redo_task(st, 2)
    assert st.report.sections[0].model_dump() == before
    assert len(fake.calls) == 5


def test_events_published(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    texts = [e.text for e in pipe.bus.history(pipe.key(st))]
    assert any("Задание 1" in t for t in texts) and any("Вёрстка" in t for t in texts)


def test_memory_updated_on_final(tmp_home, tmp_path):
    from forlabs.pipeline.memory import SubjectMemory
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS, "Сайт о Бобруйске: анимации заголовка и герба, файл style.css."])
    pipe.memory = SubjectMemory()
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    pipe.mark_final(st)
    mem = pipe.memory.read("ivanov", "psip")
    assert mem.startswith("ЛР №7: Сайт о Бобруйске")


def test_renumber_collision_never_deletes_existing(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, RAW, RAW])
    a = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())]), date="24.09.2026")
    b = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())]), date="24.09.2026")
    assert (a.number, b.number) == ("7", "7-2")
    marker = pipe.lab_dir(b) / "assets" / "shot.png"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(b"precious")
    pipe.renamed.clear()  # перезапуск приложения / ctx.reset()
    c = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())]), date="24.09.2026")
    assert c.number == "7-3"
    assert marker.exists() and marker.read_bytes() == b"precious"
    assert pipe.store.load_lab("ivanov", "psip", "7-2") is not None


def test_create_lab_with_existing_number_refuses(tmp_home, tmp_path):
    import pytest
    from forlabs.pipeline.run import PipelineError
    pipe, fake = setup(tmp_home, tmp_path, [RAW])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7"),
                    date="24.09.2026")
    with pytest.raises(PipelineError):
        pipe.create_lab("ivanov", "psip", [("x.txt", b"other")], number="7")
    assert pipe.store.load_lab("ivanov", "psip", "7").spec is not None


def test_drafts_do_not_share_source(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [])
    a = pipe.create_lab("ivanov", "psip", [("first.txt", b"a")])
    b = pipe.create_lab("ivanov", "psip", [("second.txt", b"b")])
    assert a.number != b.number
    assert sorted(p.name for p in Path(b.source_path).iterdir()) == ["second.txt"]


def test_unexpected_error_marks_failed(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, RuntimeError("что-то сломалось")])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    saved = pipe.store.load_lab("ivanov", "psip", "7")
    assert saved.status == "failed" and "сломалось" in saved.error


def test_unexpected_error_in_parse_marks_failed(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RuntimeError("нет ключа")])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7"))
    assert st.status == "failed" and "нет ключа" in st.error
