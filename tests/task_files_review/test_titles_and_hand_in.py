"""Название документа от модели, подсказка автору в режиме файлов и «Сдать» без файлов заданий."""

import shutil

import pytest
from test_task_files_stage import _folder, _on, fake_pages, files_lab

from forlabs.core.models import TaskSection
from forlabs.pipeline import taskfiles as tf
from forlabs.pipeline.write import SectionDraft, doc_title


@pytest.mark.parametrize("raw", [
    "Документация пользователя приложения",
    "Документы проекта и их назначение",
    "Документооборот предприятия",
    "Задание на проектирование приложения",
])
def test_title_is_not_cut_in_the_middle_of_a_word(raw):
    """doc_title срезает в начале «Задание» и «Документ» без границы слова: «Документация пользователя» становится
    «ация пользователя», «Задание на проектирование» — «на проектирование». Это имя файла в папке сдачи, заголовок
    документа и подпись рисунка в отчёте."""
    cleaned = doc_title(raw)
    assert cleaned == raw, tf.file_name(TaskSection(n=3, title="x", doc_title=cleaned))


def test_null_title_from_the_model_is_not_a_format_error():
    """«doc_title»: null (модель не придумала названия) — ошибка схемы: llm.json уходит в круг исправления и
    заново пишет всё задание, уже без размышления."""
    draft = SectionDraft.model_validate({"blocks": [{"type": "paragraph", "text": "Текст."}], "doc_title": None})
    assert draft.doc_title == ""


def test_writer_is_not_told_two_opposite_things(tmp_home, tmp_path):
    """Спецификация: правило «отдельных документов ForLabs не создаёт» действует только в обычном режиме. В режиме
    файлов автор задания получает в системной подсказке «ForLabs отдельных файлов не создаёт», а в запросе —
    «ForLabs сам оформит отдельным документом Word»; правка задания в чате получает только первое."""
    from test_run import ANS, FIXTURE, RAW, setup

    sec = {"doc_title": "Анимации страницы о Бобруйске", "blocks": [{"type": "paragraph", "text": "Сделаны анимации."}]}
    pipe, fake = setup(tmp_home, tmp_path, [RAW, sec, sec, ANS])
    pipe.page_renderer = fake_pages([])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())]), date="24.09.2026")
    pipe.execute(_on(pipe, st))
    wrote = [call for call in fake.calls if any("Напиши содержимое задания" in str(m["content"]) for m in call)]
    assert wrote
    for call in wrote:
        said = " ".join(" ".join(str(m["content"]).split()) for m in call)
        assert "отдельным документом Word" in said
        assert "отдельных файлов не создаёт" not in said


def test_hand_in_puts_the_task_files_back(tmp_home, tmp_path):
    """Студент убрал папку работы из папки сдачи (или перенёс файлы) и нажал «Сдать». Отчёт свежий — вёрстки нет,
    в папку сдачи ложится только отчёт со снимками документов; самих документов заданий там нет, а страница лабы
    по-прежнему перечисляет их как лежащие в папке."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st = pipe.render(_on(pipe, st))
    assert st.status == "rendered", st.error
    shutil.rmtree(_folder(tmp_path))
    st = pipe.mark_final(st)
    assert st.status == "final"
    assert (_folder(tmp_path) / "ЛР7_ПССИП.docx").is_file()
    missing = [n for n in st.task_docs if not (_folder(tmp_path) / n).is_file()]
    assert missing == [], f"сдан отчёт без файлов заданий: {missing}"


def test_switching_off_and_on_does_not_bring_pictures_back_either(tmp_home, tmp_path):
    """Тот же застрявший результат «снимков нет»: выключить и снова включить режим не помогает — index.json
    при выключении не трогается."""
    from test_task_files_stage import _texts

    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    pipe.page_renderer = lambda docx, work: []
    st = pipe.render(_on(pipe, st))
    pipe.page_renderer = fake_pages(calls)
    st = pipe.render(pipe.confirm(st, st.spec.model_copy(update={"task_files": False})))
    st = pipe.render(_on(pipe, st))
    assert "Рисунок 1 – Результат выполнения задания 1" in _texts(st.docx_path)
