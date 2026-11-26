"""Чат и переключение режима: что студенту сказано против того, что на самом деле лежит на диске."""

from pathlib import Path

from test_task_files_stage import OFF, ON, _folder, _on, _texts, files_lab

from forlabs.core.models import Paragraph, TaskSection


def test_files_note_does_not_hide_task_1_behind_task_10(tmp_home, tmp_path):
    """_files_note ищет файл задания по началу имени: «Задание 1» находит и в «Задание 10.docx». В работе из
    десяти и более заданий задание 1 без файла не попадает в «Без файла остались задания …»."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st = _on(pipe, st)
    st.report.sections = [TaskSection(n=n, title=f"Задание номер {n}.", blocks=[Paragraph(text="Текст.")])
                          for n in (1, 10)]
    folder = _folder(tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Задание 10.docx").write_bytes(b"x")  # у задания 1 файла нет (ждёт снимка студента)
    st.task_docs = ["Задание 10.docx"]
    st.status = "needs_manual"
    note = pipe._files_note(st)
    assert "Без файла остались задания 1" in note, note


def test_switch_off_note_is_true_when_the_report_was_not_rebuilt(tmp_home, tmp_path):
    """Режим выключают в чате, а вёрстка падает раньше, чем доходит до папки сдачи (нет файла рисунка): файлы
    заданий лежат как лежали, отчёт не пересобран, а в ответе — «Пересобрал отчёт: текст заданий снова в нём,
    своих файлов заданий в папке сдачи не оставил»."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path, [OFF])
    st = pipe.render(_on(pipe, st))
    fig = next(b for b in st.report.sections[0].blocks if getattr(b, "image_path", None))
    (pipe.lab_dir(st) / fig.image_path).unlink()  # снимок пропал (чистка диска, лаба с другого компьютера)
    st = pipe.chat(st, "верни как было, текстом")
    reply = st.chat[-1].text
    assert st.status == "failed"
    assert (_folder(tmp_path) / "Задание 1.docx").is_file()  # файлы на месте
    assert "не оставил" not in reply and "Пересобрал отчёт" not in reply, reply


def test_switch_on_reply_says_when_the_report_still_has_the_text(tmp_home, tmp_path):
    """Режим включают в чате на компьютере без Word и LibreOffice: файлы положены, но в отчёте остался текст
    заданий. В ответе чата — только «Файлы заданий лежат в папке …»; про то, что снимков в отчёте нет, сказано
    лишь в журнале сборки."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path, [ON])
    pipe.page_renderer = lambda docx, work: []
    st = pipe.chat(st, "сделай каждое задание отдельным файлом, в отчёт вставь их скриншоты")
    reply = st.chat[-1].text
    assert any(t.startswith("Ответ: ") for t in _texts(st.docx_path))  # в отчёте по-прежнему текст
    assert "снимк" in reply or "LibreOffice" in reply, reply


def test_unrelated_question_does_not_switch_the_mode_off(tmp_home, tmp_path):
    """Механизм, а не доказанное поведение модели: task_files — «true / false / null», и «false» вместо «null»
    (рядом в образце ответа стоит "reshoot": false) на любом сообщении выключает режим и убирает файлы из папки
    сдачи — даже если модель назвала ответ answer."""
    stray = {"kind": "answer", "reply": "Файлы лежат в папке сдачи.", "task_files": False}
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path, [stray])
    st = pipe.render(_on(pipe, st))
    st = pipe.chat(st, "где лежат документы заданий?")
    assert st.spec.task_files is True
    assert (_folder(tmp_path) / "Задание 1.docx").is_file()


def test_choice_made_after_creation_survives_a_new_methodology(tmp_home, tmp_path):
    """Спецификация: «Выбор студента переживает повторный разбор». Выбор, сделанный галочкой на вкладке разбора
    или в чате, живёт только в spec; замена методички обнуляет spec — и режим молча возвращается к настройке
    предмета (а при вёрстке свои файлы из папки сдачи убираются)."""
    from test_run import FIXTURE, RAW

    pipe, fake, st, calls = files_lab(tmp_home, tmp_path, [RAW])
    st = _on(pipe, st)
    assert st.spec.task_files is True
    st = pipe.replace_source(st, [(FIXTURE.name, FIXTURE.read_bytes())])
    assert st.status == "parsed", st.error
    assert st.spec.task_files is True


def test_numbers_of_figures_stay_unique_in_a_mixed_report(tmp_home, tmp_path):
    """Контроль: задание-снимки, задание-текст со своим рисунком и ответы-снимок — номера рисунков идут подряд."""
    from forlabs.core.models import Figure

    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st.spec.tasks[0].text = "Составьте презентацию «Анимации» (8 слайдов)."  # задание 1 остаётся текстом
    st.report.sections[0].title = st.spec.tasks[0].text
    st = pipe.render(_on(pipe, st))
    assert st.status == "rendered", st.error
    report = _texts(st.docx_path)
    captions = [t for t in report if t.startswith("Рисунок ")]
    numbers = [int(t.split()[1]) for t in captions]
    assert numbers == list(range(1, len(numbers) + 1)) and len(numbers) == 3, captions
    assert any(isinstance(b, Figure) for b in st.report.sections[0].blocks)
    from forlabs.render.conformance import compare

    assert [f for f in compare(st.docx_path) if f.severity == "error"] == []  # сверка с образцом не ругается


def test_chat_edit_says_when_the_file_in_the_folder_was_not_updated(tmp_home, tmp_path, monkeypatch):
    """Самый частый случай: студент держит «Задание 1.docx» открытым в Word и просит чат поправить это задание.
    Отчёт пересобран с новыми снимками, файл в папке сдачи остался прежним (Word его держит). В ответе чата —
    «Изменено: задание 1» и ни слова о том, что файл не обновлён; предупреждение есть только в журнале сборки."""
    import os

    edit = {"kind": "edit", "reply": "Перепишу первое задание.", "tasks": [{"n": 1, "instruction": "перепиши"}]}
    revised = {"blocks": [{"type": "paragraph", "text": "Совсем новый текст первого задания для проверки."}]}
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path, [edit, revised])
    st = pipe.render(_on(pipe, st))
    folder = _folder(tmp_path)
    before = (folder / "Задание 1.docx").read_bytes()
    real = os.replace

    def replace(src, dst, *a, **kw):
        if Path(dst).parent == folder:
            raise PermissionError(13, "Процесс не может получить доступ к файлу", str(dst))
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", replace)
    st = pipe.chat(st, "перепиши первое задание")
    reply = st.chat[-1].text
    assert (folder / "Задание 1.docx").read_bytes() == before  # файл в папке сдачи — прежний
    assert "Word" in reply or "не обнов" in reply, reply
