"""Режим «задания отдельными файлами» в конвейере: файлы в папке сдачи, снимки их страниц в отчёте."""

import base64
import io
import json
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw
from test_chat_run import ready
from test_run import png_bytes

from forlabs.pipeline import taskfiles as tf


def fake_pages(calls):
    """Вместо Word: «страницы» документа — две белые картинки с тёмным прямоугольником."""
    def render(docx: Path, work: Path) -> list[Path]:
        calls.append(docx.name)
        out = []
        (work / "pages").mkdir(parents=True, exist_ok=True)
        for i in (1, 2):
            im = Image.new("RGB", (893, 1263), (255, 255, 255))
            ImageDraw.Draw(im).rectangle((150, 120, 700, 300 + 100 * i), fill=(0, 0, 0))
            im.save(work / "pages" / f"page-{i:03d}.png")
            out.append(work / "pages" / f"page-{i:03d}.png")
        return out
    return render


def files_lab(tmp_home, tmp_path, extra=()):
    pipe, fake, st = ready(tmp_home, tmp_path, list(extra))
    calls = []
    pipe.page_renderer = fake_pages(calls)
    return pipe, fake, st, calls


def _folder(tmp_path) -> Path:
    return tmp_path / "сдача" / "ПССИП" / "ЛР7"


def _texts(docx) -> list[str]:
    import docx as docx_lib

    return [p.text for p in docx_lib.Document(str(docx)).paragraphs if p.text.strip()]


def _on(pipe, st, **more):
    return pipe.confirm(st, st.spec.model_copy(update={"task_files": True, **more}))


def test_files_appear_in_the_hand_in_folder_and_their_pictures_in_the_report(tmp_home, tmp_path):
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    before = _texts(st.docx_path)
    st = pipe.render(_on(pipe, st))
    assert st.status == "rendered", st.error
    names = sorted(f.name for f in _folder(tmp_path).glob("*.docx"))
    assert names == ["Задание 1.docx", "Задание 2.docx", "Ответы на контрольные вопросы.docx"]
    assert st.task_docs == names and len(calls) == 3
    task1 = _texts(_folder(tmp_path) / "Задание 1.docx")
    assert task1[0].startswith("Задание 1. ") and any(t in before for t in task1[1:])  # тот же текст, что был в отчёте
    report = _texts(st.docx_path)
    assert "Результат выполнения задания 1 представлен на рисунке 1" in report
    assert "Рисунок 1 – Результат выполнения задания 1" in report
    assert any(t.startswith("Рисунок") and "Ответы на контрольные вопросы" in t for t in report)
    assert not any(t.startswith("Ответ: ") for t in report)  # ответы — только снимком
    body = [t for t in task1[1:] if len(t) > 40]
    assert body and not any(t in report for t in body)  # текста задания в отчёте больше нет
    assert st.report.sections[0].blocks and st.report.questions  # а в самой лабе он цел: это исходник файла
    with zipfile.ZipFile(st.docx_path) as z:
        assert z.read("word/document.xml").count(b"<w:drawing") >= 3  # по снимку на каждое задание и на ответы


def test_unchanged_files_are_not_rebuilt_and_changed_ones_are(tmp_home, tmp_path):
    edit = {"kind": "edit", "reply": "Допишу.", "tasks": [{"n": 2, "instruction": "добавь абзац"}]}
    revised = {"blocks": [{"keep": "b1"}, {"type": "paragraph", "text": "Добавленный абзац про прокрутку страницы."}]}
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path, [edit, revised])
    st = pipe.render(_on(pipe, st))
    first = (_folder(tmp_path) / "Задание 1.docx").read_bytes()
    calls.clear()
    st = pipe.render(st)
    assert calls == []  # ничего не менялось — Word не запускается заново
    st = pipe.chat(st, "во втором задании добавь абзац")
    assert calls == ["Задание 2.docx"]  # пересобран только файл изменённого задания
    assert (_folder(tmp_path) / "Задание 1.docx").read_bytes() == first
    assert "Добавленный абзац про прокрутку страницы." in _texts(_folder(tmp_path) / "Задание 2.docx")


def test_task_waiting_for_the_students_screenshot_stays_in_the_report(tmp_home, tmp_path):
    from forlabs.core.models import Figure

    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st.report.sections[1].blocks.append(Figure(topic="Окно программы", slot="Сними окно"))
    st = pipe.render(_on(pipe, st))
    assert st.status == "needs_manual" and "Задание 2.docx" not in st.task_docs
    report = _texts(st.docx_path)
    assert any("Вставить скриншот" in t for t in report)  # задание пока обычное: студент видит, что вставить
    st = pipe.attach_screenshot(st, section=2, block=len(st.report.sections[1].blocks), image=png_bytes())
    assert st.status == "rendered" and "Задание 2.docx" in st.task_docs  # снимок вставлен — файл собран, с ним внутри
    with zipfile.ZipFile(_folder(tmp_path) / "Задание 2.docx") as z:
        assert any(n.startswith("word/media/") for n in z.namelist())


def test_stamp_from_the_profile_is_put_into_every_file(tmp_home, tmp_path):
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, "PNG")
    profile = pipe.store.get_profile("ivanov")
    profile.stamp = base64.b64encode(buf.getvalue()).decode()
    pipe.store.save_profile(profile)
    st = pipe.render(_on(pipe, st))
    for name in st.task_docs:
        with zipfile.ZipFile(_folder(tmp_path) / name) as z:
            xml = z.read("word/document.xml").decode()
            assert xml.index("<w:drawing") < xml.index("<w:t"), name  # метка — первой на странице


def test_students_own_edit_of_a_file_is_kept_as_a_copy(tmp_home, tmp_path):
    edit = {"kind": "edit", "reply": "Допишу.", "tasks": [{"n": 1, "instruction": "добавь абзац"}]}
    revised = {"blocks": [{"type": "paragraph", "text": "Совсем новый текст первого задания для проверки."}]}
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path, [edit, revised])
    st = pipe.render(_on(pipe, st))
    mine = _folder(tmp_path) / "Задание 1.docx"
    mine.write_bytes(b"my own version")
    (_folder(tmp_path) / "мой файл.docx").write_bytes(b"mine")
    st = pipe.chat(st, "перепиши первое задание")
    assert (mine.with_name(mine.name + ".bak")).read_bytes() == b"my own version"  # правка студента не пропала
    assert "Совсем новый текст" in " ".join(_texts(mine))
    assert (_folder(tmp_path) / "мой файл.docx").read_bytes() == b"mine"
    assert ".bak" in " ".join(e.text for e in pipe.bus.history(pipe.key(st)))


def test_switching_the_mode_off_takes_the_files_away_and_returns_the_text(tmp_home, tmp_path):
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st = pipe.render(_on(pipe, st))
    (_folder(tmp_path) / "мой файл.docx").write_bytes(b"mine")
    st = pipe.render(pipe.confirm(st, st.spec.model_copy(update={"task_files": False})))
    assert sorted(f.name for f in _folder(tmp_path).glob("*.docx")) == ["мой файл.docx"] and st.task_docs == []
    assert any(t.startswith("Ответ: ") for t in _texts(st.docx_path))


def test_without_word_the_files_are_made_and_the_report_keeps_the_text(tmp_home, tmp_path):
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    pipe.page_renderer = lambda docx, work: []  # ни Word, ни LibreOffice: страниц не снять
    st = pipe.render(_on(pipe, st))
    assert st.status == "rendered" and len(st.task_docs) == 3
    assert any(t.startswith("Ответ: ") for t in _texts(st.docx_path))
    journal = " ".join(e.text for e in pipe.bus.history(pipe.key(st)))
    assert "Word" in journal and "снимк" in journal


def test_presentation_task_is_left_to_the_student(tmp_home, tmp_path):
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st.spec.tasks[0].text = "Составьте презентацию «Анимации» (8 слайдов)."
    st.report.sections[0].title = st.spec.tasks[0].text
    st = pipe.render(_on(pipe, st))
    assert "Задание 1.docx" not in st.task_docs and "Задание 2.docx" in st.task_docs


def test_files_are_listed_for_the_chat(tmp_home, tmp_path):
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path, [{"kind": "answer", "reply": "Файлы в папке сдачи."}])
    st = pipe.render(_on(pipe, st))
    pipe.chat(st, "где документы заданий")
    asked = "\n".join(m["content"] for m in fake.calls[-1] if isinstance(m["content"], str))
    assert "Задания отдельными файлами: да" in asked
    assert "Файлы заданий" in asked and str(_folder(tmp_path) / "Задание 1.docx") in asked
    assert "Другие файлы в папке сдачи: Задание" not in asked


# ---------- включение, подсказки модели, соседние механизмы

ON = {"kind": "answer", "reply": "Сделаю каждое задание отдельным файлом Word.", "task_files": True}
OFF = {"kind": "edit", "reply": "Верну текст заданий в отчёт.", "task_files": False}


def test_chat_switches_the_mode_and_says_what_was_really_made(tmp_home, tmp_path):
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path, [ON, OFF])
    st = pipe.chat(st, "ВСЕ ЗАДАНИЯ должны быть в отдельном вордовском файле . у каждого задания свой файл")
    reply = st.chat[-1]
    assert st.spec.task_files is True and reply.kind == "edit" and "задания — отдельными файлами" in reply.changed
    assert str(_folder(tmp_path)) in reply.text and "Задание 1.docx" in reply.text  # путь и имена — по папке сдачи
    system = fake.calls[-1][0]["content"]
    assert '"task_files"' in system and "снимки их страниц" in system
    st = pipe.chat(st, "верни как было, текстом")
    assert st.spec.task_files is False and st.task_docs == [] and not list(_folder(tmp_path).glob("Задание*.docx"))
    st = pipe.revert(st, reply.version)  # и «Вернуть как было» у первой правки — тоже выключает
    assert st.spec.task_files is False


def test_writer_is_told_to_write_the_document_itself(tmp_home, tmp_path):
    """В режиме файлов модель пишет содержимое как сам документ и называет его; места «снимок документа в Word»
    не нужны — снимки вставит ForLabs."""
    from test_run import ANS, FIXTURE, RAW, setup

    sec = {"doc_title": "Анимации страницы о Бобруйске", "blocks": [{"type": "paragraph", "text": "Сделаны анимации."}]}
    pipe, fake = setup(tmp_home, tmp_path, [RAW, sec, sec, ANS])
    pipe.page_renderer = fake_pages([])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())]), date="24.09.2026")
    st = pipe.execute(_on(pipe, st))
    wrote = [m["content"] for call in fake.calls for m in call if "Напиши содержимое задания" in m["content"]]
    assert wrote and all("отдельным документом Word" in t and "doc_title" in t for t in wrote)
    assert st.report.sections[0].doc_title == "Анимации страницы о Бобруйске"
    assert "Задание 1 — Анимации страницы о Бобруйске.docx" in st.task_docs
    report = _texts(st.docx_path)
    assert any(t.startswith("Рисунок 1 – Анимации страницы о Бобруйске") for t in report)


def test_usual_mode_prompt_is_unchanged(tmp_home, tmp_path):
    from test_run import ANS, FIXTURE, RAW, SEC, SEC2, setup

    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())]), date="24.09.2026")
    pipe.execute(pipe.confirm(st, None))
    wrote = [m["content"] for call in fake.calls for m in call if "Напиши содержимое задания" in m["content"]]
    assert wrote and not any("отдельным документом Word" in t for t in wrote)


def test_subject_default_turns_the_mode_on_for_new_works(tmp_home, tmp_path):
    from test_run import FIXTURE, RAW, setup

    pipe, fake = setup(tmp_home, tmp_path, [RAW, RAW])
    profile = pipe.store.get_profile("ivanov")
    profile.enrollments[0].task_files = True
    pipe.store.save_profile(profile)
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())]), date="24.09.2026")
    assert st.spec.task_files is True
    off = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], task_files=False),
                     date="24.09.2026")
    assert off.spec.task_files is False  # студент выбрал сам — предмет не решает за него
    again = pipe.parse(off, date="24.09.2026")
    assert again.spec.task_files is False  # и повторный разбор выбор не теряет


def test_kind_change_moves_the_files_of_tasks_too(tmp_home, tmp_path):
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st = pipe.render(_on(pipe, st))
    st = pipe.render(pipe.confirm(st, st.spec.model_copy(update={"work_kind": "session"})))
    new = tmp_path / "сдача" / "ПССИП" / "ПЗ7"
    assert not _folder(tmp_path).exists() and sorted(f.name for f in new.glob("Задание*.docx")) == [
        "Задание 1.docx", "Задание 2.docx"]
    assert not list(new.glob("*.bak"))  # свои же файлы после переезда не приняты за правку студента
    manifest = json.loads((pipe.lab_dir(st) / "files" / "hand_in.json").read_text(encoding="utf-8"))
    assert manifest["folder"] == str(new)


def test_semester_report_uses_the_pictures_too(tmp_home, tmp_path):
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st = pipe.render(_on(pipe, st))
    out, _ = pipe.build_semester("ivanov", "psip", ["7"], tmp_path / "sem.docx")
    texts = _texts(out)
    assert any(t.startswith("Рисунок 1 – Результат выполнения задания 1") for t in texts)
    assert not any(t.startswith("Ответ: ") for t in texts)


def test_mode_turned_on_for_a_written_report_drops_places_for_word_screenshots(tmp_home, tmp_path):
    """Работа написана в обычном режиме: в задании ждёт место под «снимок документа в Word». Студент ставит галочку
    «отдельными файлами» — место уходит само, файл задания собирается, снимков от студента больше не нужно."""
    from forlabs.core.models import Figure

    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st.report.sections[1].blocks.append(Figure(topic="Техническое задание в документе MS Word",
                                               slot="Открой документ MS Word с ТЗ и сними первую страницу"))
    st = pipe.render(_on(pipe, st))
    assert st.status == "rendered", st.error
    assert "Задание 2.docx" in st.task_docs and not pipe.slots(st)
    assert not any(isinstance(b, Figure) for b in st.report.sections[1].blocks)  # и в самой лабе места больше нет
    journal = (pipe.lab_dir(st) / "journal.log").read_text(encoding="utf-8")
    assert "снимк" in journal and "Word" in journal and "задани" in journal  # студенту сказано, что убрано и почему


def test_wish_for_separate_files_turns_the_mode_on(tmp_home, tmp_path):
    """Студентка написала это в пожеланиях при создании работы — режим включается сам, и ей об этом сказано."""
    from test_run import FIXTURE, RAW, setup

    from forlabs.core.models import Enrollment
    from forlabs.core.subjects import Subject, add_custom_subject

    pipe, fake = setup(tmp_home, tmp_path, [RAW, RAW, RAW, RAW])
    add_custom_subject(Subject(id="praktika", name="Учебная практика", short_name="практика", kind="theory"))
    profile = pipe.store.get_profile("ivanov")
    profile.enrollments.append(Enrollment(subject_id="praktika", title_subject="Учебная практика",
                                          teacher="Т.Т. Тестова", academic_year="2026/2027"))
    pipe.store.save_profile(profile)
    src = [(FIXTURE.name, FIXTURE.read_bytes())]
    wish = "Все задания делаются отдельным файлом а в отчет вставляются скриншотами."
    st = pipe.parse(pipe.create_lab("ivanov", "praktika", src, wishes=wish), date="24.09.2026")
    assert st.spec.task_files is True
    journal = (pipe.lab_dir(st) / "journal.log").read_text(encoding="utf-8")
    assert "отдельными файлами" in journal and "пожелани" in journal
    off = pipe.parse(pipe.create_lab("ivanov", "praktika", src, wishes=wish, task_files=False), date="24.09.2026")
    assert off.spec.task_files is False  # выбрано «текстом в отчёте» — выбор в форме важнее фразы
    code = pipe.parse(pipe.create_lab("ivanov", "psip", src, wishes=wish), date="24.09.2026")
    assert code.spec.task_files is False  # предмет с программами: «отдельным файлом» там про файлы с кодом
    word = pipe.parse(pipe.create_lab("ivanov", "psip", src, wishes="каждое задание отдельным документом Word"),
                      date="24.09.2026")
    assert word.spec.task_files is True
