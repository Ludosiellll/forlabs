"""files/index.json: что остаётся устаревшим и что происходит, когда документ задания не снять."""

import json

from test_task_files_stage import _folder, _on, _texts, fake_pages, files_lab


def _journal(pipe, st) -> str:
    return " ".join(e.text for e in pipe.bus.history(pipe.key(st)))


def test_pictures_appear_after_word_or_libreoffice_is_installed(tmp_home, tmp_path):
    """Первый раз снять страницы было нечем (нет ни Word, ни LibreOffice). Студент ставит LibreOffice, как ему
    и советуют, и жмёт «Пересобрать документ» — в отчёте по-прежнему текст: пустой результат «снимков нет»
    записан в index.json как готовый и больше не пересчитывается, пока не изменится само задание."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    pipe.page_renderer = lambda docx, work: []
    st = pipe.render(_on(pipe, st))
    assert any(t.startswith("Ответ: ") for t in _texts(st.docx_path))  # снимать нечем — в отчёте текст
    pipe.page_renderer = fake_pages(calls)  # LibreOffice поставлен
    st = pipe.render(st)
    assert calls, "документы заданий даже не пробовали снять заново"
    report = _texts(st.docx_path)
    assert "Рисунок 1 – Результат выполнения задания 1" in report
    assert not any(t.startswith("Ответ: ") for t in report)


def test_one_document_without_pictures_is_reported(tmp_home, tmp_path):
    """Снять не получилось один документ из трёх (Word завис на нём): в отчёте это задание осталось текстом, остальные
    — снимками, и студенту об этом не сказано ни слова (предупреждение есть только когда не снят ни один)."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    pages = fake_pages(calls)
    pipe.page_renderer = lambda docx, work: [] if work.name == "2" else pages(docx, work)
    st = pipe.render(_on(pipe, st))
    assert st.status == "rendered", st.error
    report = _texts(st.docx_path)
    assert "Рисунок 1 – Результат выполнения задания 1" in report
    assert not any("Результат выполнения задания 2" in t for t in report)  # задание 2 — текстом
    said = _journal(pipe, st)
    assert "снимк" in said or "Задание 2" in said.split("Собираю файл: Ответы на контрольные вопросы.docx")[-1]


def test_a_failing_document_does_not_take_the_whole_report_down(tmp_home, tmp_path):
    """Снимок страниц одного документа бросил исключение (битый PDF, сбой Pillow, занятый файл): вместо отчёта
    с текстом этого задания студент получает «документ не собрался» целиком."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    pages = fake_pages(calls)

    def render(docx, work):
        if work.name == "2":
            raise RuntimeError("cannot open broken document")
        return pages(docx, work)

    pipe.page_renderer = render
    st = pipe.render(_on(pipe, st))
    assert st.status == "rendered", st.error


def test_semester_report_does_not_use_pictures_of_an_older_text(tmp_home, tmp_path):
    """_shown_report берёт снимки из index.json, не сверяя их с текстом задания. Задание переписано, а вёрстка
    после этого не прошла (или ещё не запускалась) — общий отчёт собирается со снимками прежнего текста, хотя
    в обычном режиме он взял бы новый текст."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st = pipe.render(_on(pipe, st))
    st.report.sections[1].blocks[0].text = "Совсем другой текст второго задания, написанный позже."
    pipe.store.save_lab(st)  # как после «Переписать задание», у которого вёрстка не прошла
    index = json.loads((pipe.lab_dir(st) / "files" / "index.json").read_text(encoding="utf-8"))
    old_shots = {p for p, _ in index["2"]["shots"]}
    shown = pipe._shown_report(st, pipe.lab_dir(st))
    used = {b.image_path for b in shown.sections[1].blocks if getattr(b, "image_path", None)}
    assert not (used & old_shots)  # снимки прежнего текста выдавать за нынешний нельзя: либо текст, либо пересборка


def test_first_render_makes_each_document_once(tmp_home, tmp_path):
    """Контроль: лишних пересборок нет — вторая вёрстка без изменений не трогает ни одного документа, смена
    вёрстки отчёта (plain) и вида работы — тоже."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st = pipe.render(_on(pipe, st))
    assert sorted(calls) == ["1", "2", "answers"]
    calls.clear()
    st = pipe.render(pipe.confirm(st, st.spec.model_copy(update={"plain_layout": True, "date": "01.10.2026"})))
    assert calls == []
    assert sorted(f.name for f in _folder(tmp_path).glob("*.docx")) == [
        "Задание 1.docx", "Задание 2.docx", "Ответы на контрольные вопросы.docx"]


def test_stamp_and_author_changes_rebuild_the_documents(tmp_home, tmp_path):
    """Контроль: смена метки и автора в профиле пересобирает документы (и только тогда)."""
    import base64
    import io

    from PIL import Image

    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st = pipe.render(_on(pipe, st))
    calls.clear()
    profile = pipe.store.get_profile("ivanov")
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, "PNG")
    profile.stamp = base64.b64encode(buf.getvalue()).decode()
    pipe.store.save_profile(profile)
    st = pipe.render(st)
    assert len(calls) == 3
    calls.clear()
    profile.doc_author = "Другой Автор"
    pipe.store.save_profile(profile)
    st = pipe.render(st)
    assert len(calls) == 3
    calls.clear()
    st = pipe.render(st)
    assert calls == []


def test_lab_made_before_the_feature_renders_and_touches_nothing(tmp_home, tmp_path):
    """Контроль: состояние без новых полей (лаба из прежней версии) читается и верстается; папка сдачи не создаётся."""
    from forlabs.core.models import LabState

    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    raw = json.loads(st.model_dump_json())
    for key in ("task_docs", "task_files"):
        raw.pop(key)
    raw["spec"].pop("task_files")
    for sec in raw["report"]["sections"]:
        sec.pop("doc_title")
    raw["report"].pop("answer_figures")
    old = LabState.model_validate(raw)
    old = pipe.render(old)
    assert old.status == "rendered", old.error
    assert old.task_docs == [] and not _folder(tmp_path).exists() and calls == []
