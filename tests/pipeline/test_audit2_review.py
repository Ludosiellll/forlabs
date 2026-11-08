"""Финальное ревью аудита-2: вопросы после «Сохранить разбор», правки в Word, профиль, «Выполнить заново»."""

import zipfile
from pathlib import Path

import pytest
from test_run import ANS, FIXTURE, RAW, SEC, SEC2, png_bytes, setup

from forlabs.pipeline.run import lab_complete


def rendered(tmp_home, tmp_path, extra=()):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS, *extra])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    assert st.status == "rendered", st.error
    return pipe, fake, st


def docx_text(p: Path) -> str:
    return zipfile.ZipFile(p).read("word/document.xml").decode("utf-8")


# 2: изменённый вопрос не остаётся старым в документе
def test_changed_question_requires_new_answer(tmp_home, tmp_path):
    pipe, fake, st = rendered(tmp_home, tmp_path, extra=[{"answers": [{"n": 1, "answer": "Новый ответ."}]}])
    spec = st.spec.model_copy(deep=True)
    spec.questions = ["Что такое анимация?"]
    st = pipe.confirm(st, spec)
    assert not lab_complete(st) and st.status == "failed" and "Продолжить" in st.error
    st = pipe.execute(st)
    assert st.report.questions[0].question == "Что такое анимация?"
    assert st.report.questions[0].answer == "Новый ответ."


# 3: правки, сделанные в Word, не пропадают молча при пересборке
def test_manual_word_edits_are_kept_aside(tmp_home, tmp_path):
    pipe, fake, st = rendered(tmp_home, tmp_path)
    docx = pipe.lab_dir(st) / "report.docx"
    docx.write_bytes(docx.read_bytes() + b"\x00")  # студент открыл в Word и сохранил
    pipe.mark_final(st)
    kept = list(pipe.lab_dir(st).glob("report.manual-*.docx"))
    assert kept
    assert any("Word" in e.text and "manual" in e.text for e in pipe.bus.history(pipe.key(st)))


# 4: поправили профиль (группу) после вёрстки — «Сдать» пересобирает документ
def test_profile_change_rebuilds_on_submit(tmp_home, tmp_path):
    pipe, fake, st = rendered(tmp_home, tmp_path)
    prof = pipe.store.get_profile("ivanov")
    prof.group = "РС99-99"
    pipe.store.save_profile(prof)
    pipe.mark_final(st)
    out = next((tmp_path / "сдача").rglob("*.docx"))
    assert "РС99-99" in docx_text(out)


# мелкое: «Выполнить заново» на упавшей, но дописанной лабе переписывает текст
def test_full_rerun_on_failed_complete_lab(tmp_home, tmp_path):
    new_sec = {"blocks": [{"type": "paragraph", "text": "Совсем новый текст первого задания."}]}
    pipe, fake, st = rendered(tmp_home, tmp_path, extra=[new_sec, SEC2, ANS])
    st.status, st.error = "failed", "что-то сломалось"
    st = pipe.execute(st, full=True)
    assert "Совсем новый" in st.report.sections[0].blocks[0].text


# мелкое: во время переписывания старый документ не выдаётся за готовый
def test_execute_marks_docx_stale_at_start(tmp_home, tmp_path, monkeypatch):
    import forlabs.pipeline.run as run

    class PowerOff(BaseException):
        pass

    pipe, fake, st = rendered(tmp_home, tmp_path)
    monkeypatch.setattr(run, "write_report", lambda *a, **k: (_ for _ in ()).throw(PowerOff()))
    with pytest.raises(PowerOff):
        pipe.execute(st, only=[1])
    assert pipe.store.load_lab("ivanov", "psip", "7").docx_sha is None


# мелкое: файлы вёрстки отчёта за семестр не попадают в папку сдачи
def test_semester_render_files_not_in_handin_folder(tmp_home, tmp_path):
    pipe, fake, st = rendered(tmp_home, tmp_path)
    out = tmp_path / "сдача" / "ПССИП" / "семестр.docx"
    pipe.build_semester("ivanov", "psip", ["7"], out)
    assert [p.name for p in out.parent.iterdir()] == ["семестр.docx"]


# мелкое: пустая таблица не оставляет подпись «Таблица N» без таблицы
def test_empty_table_has_no_caption(tmp_path):
    from forlabs.core.models import Paragraph, ReportDoc, Table, TaskSection
    from forlabs.render.docx_builder import build_lab

    doc = ReportDoc(lab_number="1", header={"Тема": "т"}, conclusion="Вывод.", sections=[TaskSection(
        n=1, title="t", blocks=[Paragraph(text="Текст."), Table(topic="пусто", rows=[])])])
    assert "Таблица 1" not in docx_text(build_lab(doc, tmp_path / "r.docx", author="x"))
