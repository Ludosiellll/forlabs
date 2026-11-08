"""Аудит-2: целостность лабы — устаревший документ, перезапуск, пустые ответы, номера, память."""

import hashlib
import threading
from pathlib import Path

import pytest
from test_run import ANS, FIXTURE, RAW, SEC, SEC2, png_bytes, setup

from forlabs.core.models import Paragraph
from forlabs.pipeline.run import PipelineError, lab_complete


class PowerOff(BaseException):
    """Выключение питания: не Exception — его не ловит ни один обработчик."""


def done_lab(tmp_home, tmp_path, extra=()):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS, *extra])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    assert st.status == "rendered", st.error
    return pipe, fake, st


def sha(p: Path) -> str:
    return hashlib.sha1(Path(p).read_bytes()).hexdigest()


# A1: правка сохранена, вёрстку оборвали — «Сдать» не должен отдать старый документ
def test_interrupted_render_after_screenshot_is_not_submitted_stale(tmp_home, tmp_path, monkeypatch):
    import forlabs.pipeline.run as run

    pipe, fake, st = done_lab(tmp_home, tmp_path)
    docx = pipe.lab_dir(st) / "report.docx"
    before = sha(docx)
    real_build = run.build_lab
    monkeypatch.setattr(run, "build_lab", lambda *a, **k: (_ for _ in ()).throw(PowerOff()))
    with pytest.raises(PowerOff):
        pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    monkeypatch.setattr(run, "build_lab", real_build)
    saved = pipe.store.load_lab("ivanov", "psip", "7")
    assert not run.docx_fresh(saved, pipe.lab_dir(saved))
    pipe.mark_final(saved)
    target = next((tmp_path / "сдача").rglob("*.docx"))
    assert sha(target) != before  # пересобран с новым снимком


def test_fresh_docx_after_render(tmp_home, tmp_path):
    from forlabs.pipeline.run import docx_fresh

    pipe, fake, st = done_lab(tmp_home, tmp_path)
    assert docx_fresh(st, pipe.lab_dir(st))
    (pipe.lab_dir(st) / "report.docx").write_bytes(b"PK old from another computer")
    assert not docx_fresh(st, pipe.lab_dir(st))


# A2: битый state.json + новая лаба с тем же номером — старые файлы не стираются
def test_renumber_keeps_unreadable_lab_files(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [])
    from forlabs.core.models import LabState

    pipe.store.save_lab(LabState(profile_id="ivanov", subject_id="psip", number="7", status="final"))
    d7 = pipe.store.lab_dir("ivanov", "psip", "7")
    (d7 / "assets").mkdir()
    (d7 / "assets" / "fig.png").write_bytes(b"png")
    (d7 / "state.json").write_text('{"profile_id": "iva', encoding="utf-8")  # обрезан при выключении
    draft = pipe.create_lab("ivanov", "psip", [("m.txt", b"x")])
    st = pipe._renumber(draft, "7", pipe.lab_dir(draft))
    assert st.number == "7"
    kept = [p for p in d7.parent.iterdir() if p.name.startswith("ЛР7.old-")]
    assert kept and (kept[0] / "assets" / "fig.png").read_bytes() == b"png"


def test_create_with_number_keeps_unreadable_lab_files(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [])
    d7 = pipe.store.lab_dir("ivanov", "psip", "7")
    (d7 / "source").mkdir(parents=True)
    (d7 / "source" / "старая.txt").write_text("старая методичка", encoding="utf-8")
    (d7 / "state.json").write_text("{битый", encoding="utf-8")
    pipe.create_lab("ivanov", "psip", [("m.txt", b"x")], number="7")
    kept = [p for p in d7.parent.iterdir() if p.name.startswith("ЛР7.old-")]
    assert kept and (kept[0] / "source" / "старая.txt").exists()


# A3: «Выполнить заново» оборвался — следующий запуск пишет заново, а не отдаёт старый текст
def test_failed_full_rerun_does_not_keep_old_report(tmp_home, tmp_path):
    from forlabs.core.llm import LLMError

    new_sec = {"blocks": [{"type": "paragraph", "text": "Новый текст первого задания после правки разбора."}]}
    pipe, fake, st = done_lab(tmp_home, tmp_path,
                              extra=[LLMError("network", "обрыв"), new_sec, SEC2, ANS])
    spec = st.spec.model_copy(deep=True)
    spec.wishes = "Сделай про Минск"
    st = pipe.confirm(st, spec)
    st = pipe.execute(st)  # первый вызов модели обрывается
    assert st.status == "failed"
    assert not lab_complete(st)  # старый отчёт не считается готовым
    st = pipe.execute(st)  # «Продолжить»
    assert st.status in ("needs_manual", "rendered"), st.error
    assert "Новый текст" in st.report.sections[0].blocks[0].text
    assert any("до выполнения заново" in v.label for v in st.versions)  # старый отчёт можно вернуть


# A4: добавленный на вкладке «Разбор» вопрос получает ответ при «Продолжить»
def test_added_question_is_answered_on_continue(tmp_home, tmp_path):
    pipe, fake, st = done_lab(tmp_home, tmp_path,
                              extra=[{"answers": ["Смена вида при наведении.", "Повторение действий."]}])
    spec = st.spec.model_copy(deep=True)
    spec.questions.append("Что такое цикл?")
    st = pipe.confirm(st, spec)
    st = pipe.render(st)
    assert st.status == "failed"
    st = pipe.execute(st)
    assert lab_complete(st) and st.status in ("rendered", "needs_manual"), st.error
    assert len(st.report.questions) == 2


# A5: пустые ответы, пустое задание, отказ модели
def test_empty_answers_are_asked_again(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, {"answers": [""]}, {"answers": ["Ответ со второго раза."]}])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    assert st.report.questions[0].answer == "Ответ со второго раза."


def test_blank_answer_or_empty_task_is_not_complete(tmp_home, tmp_path):
    pipe, fake, st = done_lab(tmp_home, tmp_path)
    blank = st.model_copy(deep=True)
    blank.report.questions[0].answer = ""
    assert not lab_complete(blank)
    empty = st.model_copy(deep=True)
    empty.report.sections[1].blocks = []
    assert not lab_complete(empty)


def test_refusal_is_an_error_finding(tmp_home, tmp_path):
    from forlabs.report.standard import check_standard

    pipe, fake, st = done_lab(tmp_home, tmp_path)
    st.report.sections[1].blocks = [Paragraph(text="Извините, но я не могу выполнить это задание.")]
    ids = {f.rule_id for f in check_standard(st.report, st.spec, "m")}
    assert "task.refusal" in ids


def test_unknown_answers_key_is_repaired():
    import json

    import httpx

    from forlabs.core.llm import LLM
    from forlabs.core.settings import Settings
    from forlabs.pipeline.write import AnswersDraft

    replies = [{"ответы": ["Не тот ключ."]}, {"answers": [{"n": 1, "answer": "Смена вида."}]}]

    def handler(req):
        body = json.dumps(replies.pop(0), ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": body}, "finish_reason": "stop"}]})

    llm = LLM(Settings(), api_key="k", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    draft = llm.json([{"role": "user", "content": "?"}], AnswersDraft)
    assert draft.for_questions([1]) == {1: "Смена вида."} and not replies


def test_execute_without_tasks_is_refused(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7"),
                    date="24.09.2026")
    spec = st.spec.model_copy(deep=True)
    spec.tasks = []
    st = pipe.execute(pipe.confirm(st, spec))
    assert st.status == "failed" and "нет заданий" in st.error


# A6: «Сдать» недописанную лабу нельзя
def test_mark_final_requires_complete_lab(tmp_home, tmp_path):
    pipe, fake, st = done_lab(tmp_home, tmp_path)
    st.report.questions = []
    with pytest.raises(PipelineError):
        pipe.mark_final(st)


# E1: модель видит текст методички и строку «Задание», когда пишет задания и ответы
def test_writer_sees_methodology_and_assignment(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    write_prompt = fake.calls[1][-1]["content"]
    answers_prompt = fake.calls[3][-1]["content"]
    marker = FIXTURE.read_text(encoding="utf-8").strip().splitlines()[3][:40]
    assert "Задания выполняются к своей теме из ЛР №2" in write_prompt
    assert marker in write_prompt and marker in answers_prompt


# E9: замечания разбора («не дословно», вариант) не пропадают после выполнения
def test_spec_findings_survive_execute(tmp_home, tmp_path):
    from forlabs.core.models import Finding

    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7"),
                    date="24.09.2026")
    st.findings.append(Finding(stage="spec", rule_id="spec.variant_workplace", severity="warn",
                               location="Вариант", message="Нужен номер рабочего места"))
    st = pipe.execute(pipe.confirm(st, None))
    assert any(f.rule_id == "spec.variant_workplace" for f in st.findings)


# A10: память предмета — фоновое обновление видно как работа и не теряет соседнее
def test_memory_update_is_tracked_and_serialized(tmp_home, tmp_path):
    started, release, overlap = threading.Event(), threading.Event(), []
    active = []

    class SlowMemory:
        def update(self, st, llm):
            active.append(1)
            overlap.append(len(active))
            started.set()
            release.wait(5)
            active.pop()

        def read(self, *a):
            return ""

    pipe, fake, st = done_lab(tmp_home, tmp_path)
    pipe.memory = SlowMemory()
    pipe.mark_final(st)
    assert started.wait(5)
    assert pipe.background  # закрытие окна спросит подтверждение
    pipe.mark_final(st)  # повторная сдача, пока первое обновление идёт
    release.set()
    for t in list(pipe._threads):
        t.join(5)
    assert not pipe.background and max(overlap) == 1


def test_memory_summary_is_capped(tmp_home):
    from forlabs.pipeline.memory import MAX_LAB_SUMMARY, _merge

    old = "ЛР №1: тема сайта — Минск.\nЛР №2: палитра синяя."
    merged = _merge(old, "3", "x" * 10_000)
    assert "ЛР №1" in merged and "ЛР №2" in merged and len(merged) < len(old) + MAX_LAB_SUMMARY + 50
