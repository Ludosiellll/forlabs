"""Выключение или обрыв посреди работы: ничего не теряется, неполный отчёт не сдаётся, всё продолжается."""

import pytest
from test_run import ANS, FIXTURE, RAW, SEC, SEC2, png_bytes, setup

from forlabs.cloud.auth import CloudError
from forlabs.core.models import QA, ReportDoc, TaskSection
from forlabs.report.standard import check_standard


class PowerOff(BaseException):
    """Процесс убит: не Exception, никакой except его не перехватывает."""


def test_missing_answers_are_an_error():
    from test_write import SPEC

    doc = ReportDoc(lab_number="8", conclusion="x", sections=[TaskSection(n=t.n, title=t.text) for t in SPEC.tasks])
    assert any(f.rule_id == "qa.missing" for f in check_standard(doc, SPEC, "f"))
    doc.questions = [QA(n=1, question=SPEC.questions[0], answer="Блок кода.")]
    assert not any(f.rule_id == "qa.missing" for f in check_standard(doc, SPEC, "f"))


def test_render_refuses_incomplete_report(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    st.report.questions = []  # как после выключения до ответов на вопросы
    st = pipe.render(st)
    assert st.status == "failed" and "Продолжить" in st.error


def test_execute_saves_full_report_before_render(tmp_home, tmp_path, monkeypatch):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.confirm(pipe.parse(st, date="24.09.2026"), None)

    def power_off(*a, **k):
        raise PowerOff()

    monkeypatch.setattr(pipe, "render", power_off)
    with pytest.raises(PowerOff):
        pipe.execute(st)
    saved = pipe.store.load_lab("ivanov", "psip", "7")
    assert len(saved.report.questions) == 1 and saved.report.questions[0].answer


def test_screenshot_saved_before_render(tmp_home, tmp_path, monkeypatch):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))

    def power_off(*a, **k):
        raise PowerOff()

    monkeypatch.setattr(pipe, "render", power_off)
    with pytest.raises(PowerOff):
        pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    saved = pipe.store.load_lab("ivanov", "psip", "7")
    assert saved.report.sections[0].blocks[3].image_path


def test_fail_does_not_raise_when_saving_fails(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")

    def offline(*a, **k):
        raise CloudError("Нет связи с облаком. Проверь интернет.")

    pipe.store.save_lab = offline
    st = pipe._fail(st, RuntimeError("что-то сломалось"))
    assert st.status == "failed"


def _webp():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (800, 500), (200, 220, 240)).save(buf, format="WEBP")
    return buf.getvalue()


def test_webp_screenshot_is_converted(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    st = pipe.attach_screenshot(st, section=1, block=4, image=_webp(), suffix=".webp")
    assert st.status == "rendered", st.error
    assert st.report.sections[0].blocks[3].image_path.endswith(".png")


def test_not_an_image_is_refused_and_slot_stays(tmp_home, tmp_path):
    from forlabs.pipeline.run import PipelineError

    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    with pytest.raises(PipelineError):
        pipe.attach_screenshot(st, section=1, block=4, image=b"<html>not an image</html>")
    assert pipe.slots(st)


def test_broken_image_file_becomes_a_slot_again(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    (pipe.lab_dir(st) / st.report.sections[0].blocks[3].image_path).write_bytes(b"\x89PNG broken")
    st = pipe.render(st)
    assert st.status == "needs_manual", st.error
    assert pipe.slots(st) and "вставь снимок ещё раз" in pipe.slots(st)[0][2].slot
