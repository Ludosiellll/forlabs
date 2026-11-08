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
    import time

    for _ in range(100):  # память обновляется в фоне после сдачи
        mem = pipe.memory.read("ivanov", "psip")
        if mem:
            break
        time.sleep(0.05)
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
    pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7"),
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


class SharedStore(Store):
    """Два компьютера с общим облаком: состояние лаб и файлы общие, папки лаб у каждого свои."""

    def __init__(self, device_root: Path, shared: dict):
        super().__init__()
        self.device_root, self.shared = device_root, shared
        shared.setdefault("labs", {})
        shared.setdefault("files", {})

    def lab_dir(self, profile_id, subject_id, number):
        return self.device_root / profile_id / subject_id / number

    def save_lab(self, st):
        self.lab_dir(st.profile_id, st.subject_id, st.number).mkdir(parents=True, exist_ok=True)
        self.shared["labs"][(st.profile_id, st.subject_id, st.number)] = st.model_dump_json()

    def load_lab(self, profile_id, subject_id, number, strict=False):
        from forlabs.core.models import LabState

        raw = self.shared["labs"].get((profile_id, subject_id, number))
        return LabState.model_validate_json(raw) if raw else None

    def delete_lab(self, profile_id, subject_id, number):
        self.shared["labs"].pop((profile_id, subject_id, number), None)

    def upload(self, local, profile_id, subject_id, number, rel):
        self.shared["files"][(profile_id, subject_id, number, rel)] = Path(local).read_bytes()
        return rel

    def fetch(self, profile_id, subject_id, number, rel, local) -> bool:
        data = self.shared["files"].get((profile_id, subject_id, number, rel))
        if data is None:
            return False
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_bytes(data)
        return True


SEC_DIAGRAM = {"blocks": [
    {"type": "paragraph", "text": "Схема показывает связь страниц сайта о Бобруйске."},
    {"type": "figure", "kind": "diagram", "topic": "Схема страниц сайта", "dot": "digraph { a -> b }"},
    {"type": "figure", "kind": "screenshot", "topic": "Анимация заголовка в окне браузера",
     "instruction": "Открой index.html в браузере"},
]}


def test_lab_files_follow_to_another_computer(tmp_home, tmp_path, monkeypatch):
    import forlabs.pipeline.write as write_mod

    def fake_dot(dot, out):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(png_bytes())
        return Path(out)

    monkeypatch.setattr(write_mod, "render_dot", fake_dot)
    shared: dict = {}
    pipe_a, _ = setup(tmp_home, tmp_path, [RAW, SEC_DIAGRAM, SEC2, ANS])
    pipe_a.store = SharedStore(tmp_path / "A", shared)
    pipe_a.render_diagrams = True
    st = pipe_a.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())])
    st = pipe_a.execute(pipe_a.confirm(pipe_a.parse(st, date="24.09.2026"), None))
    assert st.status == "needs_manual", st.error
    st = pipe_a.attach_screenshot(st, section=1, block=3, image=png_bytes())
    assert st.status == "rendered", st.error
    assert ("ivanov", "psip", "7", "assets/scheme_1_2.png") in shared["files"]

    # второй компьютер: те же данные в облаке, пустая папка лабы
    pipe_b, _ = setup(tmp_home, tmp_path, [])
    pipe_b.store = SharedStore(tmp_path / "B", shared)
    st_b = pipe_b.store.load_lab("ivanov", "psip", "7")
    st_b = pipe_b.render(st_b)
    assert st_b.status == "rendered", st_b.error
    st_b = pipe_b.mark_final(st_b)
    assert st_b.status == "final"
    out = tmp_path / "сдача" / "ПССИП" / "ЛР7" / "ЛР7_ПССИП.docx"
    xml = zipfile.ZipFile(out).read("word/document.xml").decode()
    assert xml.count("<pic:pic") == 2 and "Вставить скриншот" not in xml


def test_reparse_on_another_computer_uses_saved_text(tmp_home, tmp_path):
    shared: dict = {}
    pipe_a, _ = setup(tmp_home, tmp_path, [RAW])
    pipe_a.store = SharedStore(tmp_path / "A", shared)
    st = pipe_a.parse(pipe_a.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())]),
                      date="24.09.2026")
    assert st.status == "parsed"
    import shutil

    shutil.rmtree(tmp_path / "A")  # методичка осталась на первом компьютере
    pipe_b, _ = setup(tmp_home, tmp_path, [RAW])
    pipe_b.store = SharedStore(tmp_path / "B", shared)
    st_b = pipe_b.parse(pipe_b.store.load_lab("ivanov", "psip", "7"), date="24.09.2026")
    assert st_b.status == "parsed", st_b.error


def test_final_rerenders_stale_docx_with_screenshot_slot(tmp_home, tmp_path):
    from forlabs.render.docx_builder import build_lab

    pipe, _ = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())])
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    stale = st.model_copy(deep=True)  # docx свёрстан до вставки скриншота
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    build_lab(stale.report, Path(st.docx_path), author="x")
    st = pipe.mark_final(st)
    out = tmp_path / "сдача" / "ПССИП" / "ЛР7" / "ЛР7_ПССИП.docx"
    assert "Вставить скриншот" not in zipfile.ZipFile(out).read("word/document.xml").decode()


def test_render_uses_current_profile_and_spec(tmp_home, tmp_path):
    from forlabs.core.models import Finding

    pipe, _ = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())])
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    # после выполнения поменяли профиль (другой человек) и дату в разборе
    p = pipe.store.get_profile("ivanov")
    pipe.store.save_profile(p.model_copy(update={"gender": "f", "group": "РС03-24"}))
    st.spec.date = "01.10.2026"
    st.findings = [Finding(stage="standard", rule_id="conclusion.formula", message="устарело")]
    st = pipe.render(st)
    assert st.report.conclusion == "Сформировала умение создавать визуальные эффекты и анимации на HTML – странице."
    assert st.report.header["Номер учебной группы"] == "РС03-24"
    assert st.report.header["Дата выполнения работы"] == "01.10.2026"
    assert not [f for f in st.findings if f.rule_id == "conclusion.formula"]
    xml = zipfile.ZipFile(st.docx_path).read("word/document.xml").decode()
    assert "Сформировала" in xml and "01.10.2026" in xml


def test_failure_mid_lab_resumes_from_unwritten_task(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, LLMError("network", "нет сети")])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    assert st.status == "failed"
    saved = pipe.store.load_lab("ivanov", "psip", "7")
    assert saved.report is not None and [s.n for s in saved.report.sections] == [1]
    first = saved.report.sections[0].model_dump()
    fake.responses += [SEC2, ANS]
    st = pipe.execute(saved)
    assert st.status == "needs_manual", st.error
    assert st.report.sections[0].model_dump() == first
    assert len(fake.calls) == 5  # задание 1 не переписывалось


def test_render_checks_final_docx_against_canon(tmp_home, tmp_path, monkeypatch):
    import pytest
    from docx import Document
    from docx.shared import Pt

    import forlabs.pipeline.run as run_mod
    from forlabs.pipeline.run import PipelineError

    real = run_mod.finalize

    def broken_finalize(docx, *a, **k):
        rep = real(docx, *a, **k)
        d = Document(str(docx))  # доводка испортила оформление абзаца
        for par in d.paragraphs:
            if par.text.startswith("Фон шапки"):
                for r in par.runs:
                    r.font.size = Pt(12)
        d.save(str(docx))
        return rep

    monkeypatch.setattr(run_mod, "finalize", broken_finalize)
    pipe, _ = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    bad = [f for f in st.findings if f.rule_id == "render.canon_mismatch"]
    assert bad and "Фон шапки" in bad[0].text and bad[0].severity == "error"
    with pytest.raises(PipelineError):
        pipe.mark_final(st)


def _rendered(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    assert st.status == "rendered", st.error
    return pipe, st


def test_final_refuses_failed_lab(tmp_home, tmp_path):
    import pytest

    from forlabs.pipeline.run import PipelineError

    pipe, st = _rendered(tmp_home, tmp_path)
    st.status, st.error = "failed", "вёрстка упала"
    with pytest.raises(PipelineError):
        pipe.mark_final(st)


def test_final_with_target_open_in_word_is_a_message(tmp_home, tmp_path, monkeypatch):
    import pytest

    import forlabs.pipeline.run as run_mod
    from forlabs.pipeline.run import PipelineError

    pipe, st = _rendered(tmp_home, tmp_path)

    def locked(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(run_mod.shutil, "copyfile", locked)
    with pytest.raises(PipelineError) as e:
        pipe.mark_final(st)
    assert "Word" in str(e.value)


def test_accepting_one_finding_does_not_accept_the_rule_everywhere(tmp_home, tmp_path):
    import pytest

    from forlabs.core.models import Finding, finding_key
    from forlabs.pipeline.run import PipelineError

    pipe, st = _rendered(tmp_home, tmp_path)
    a = Finding(stage="antiai", rule_id="ai.banned_word", location="Задание 1, блок 1", text="является",
                message="Слово «является»")
    b = Finding(stage="antiai", rule_id="ai.banned_word", location="Задание 2, блок 1", text="ключевой",
                message="Слово «ключевой»")
    st.findings = [a, b]
    with pytest.raises(PipelineError):
        pipe.mark_final(st, accept_rules=[finding_key(a)])
    st = pipe.mark_final(st, accept_rules=[finding_key(a), finding_key(b)])
    assert st.status == "final"


def test_semester_refuses_labs_without_screenshots(tmp_home, tmp_path):
    import pytest

    from forlabs.pipeline.run import PipelineError

    pipe, _ = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    assert pipe.slots(st)
    with pytest.raises(PipelineError) as e:
        pipe.build_semester("ivanov", "psip", ["7"], tmp_path / "sem.docx")
    assert "ЛР 7" in str(e.value)


def test_pipeline_tags_llm_calls(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    assert fake.headers == {"X-ForLabs-Lab": "ivanov%2Fpsip%2F7"}


def test_draft_usage_is_retagged_after_renumber(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW])
    calls = []
    pipe.store.retag_usage = lambda old, new: calls.append((old, new))
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())])
    draft_key = pipe.key(st)
    st = pipe.parse(st, date="24.09.2026")
    assert st.number == "7" and calls == [(draft_key, "ivanov/psip/7")]


def test_lab_tag_only_for_cloud_proxy(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    fake.accepts_lab_tag = False  # локальный режим: запросы идут прямо провайдеру
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    assert fake.headers == {}
