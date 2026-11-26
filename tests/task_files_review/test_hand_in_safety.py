"""Папка сдачи: что _hand_in_task_files делает с файлами студента и соседних работ."""

import os
from pathlib import Path

from test_run import ANS, FIXTURE, RAW, SEC, SEC2, png_bytes
from test_task_files_stage import _folder, _on, files_lab


def _journal(pipe, st) -> str:
    return " ".join(e.text for e in pipe.bus.history(pipe.key(st)))


def test_students_edit_survives_a_render_that_did_not_change_this_task(tmp_home, tmp_path):
    """Студент поправил файл задания в папке сдачи. Любая следующая вёрстка (здесь — просто «Пересобрать документ»,
    документ задания ForLabs при этом НЕ пересобирал) заменяет его версией ForLabs."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st = pipe.render(_on(pipe, st))
    mine = _folder(tmp_path) / "Задание 1.docx"
    mine.write_bytes(b"student edit")
    calls.clear()
    st = pipe.render(st)
    assert calls == []  # документ задания не пересобирался: менять файл в папке сдачи было не на что
    assert mine.read_bytes() == b"student edit"


def test_earlier_bak_is_not_overwritten_by_a_later_one(tmp_home, tmp_path):
    """Студент поправил файл, потом задание изменилось — правка ушла в .bak. Он поправил новый файл ещё раз,
    задание изменилось снова: вторая копия не затирает первую."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st = pipe.render(_on(pipe, st))
    mine = _folder(tmp_path) / "Задание 2.docx"
    mine.write_bytes(b"first edit: an evening of work")
    st.report.sections[1].blocks[0].text = "Задание переписано первый раз, текст другой."
    st = pipe.render(st)
    assert mine.read_bytes() != b"first edit: an evening of work"  # задание изменилось — файл собран заново
    mine.write_bytes(b"second edit: two quick fixes")
    st.report.sections[1].blocks[0].text = "Задание переписано второй раз, текст снова другой."
    st = pipe.render(st)
    kept = [f.read_bytes() for f in _folder(tmp_path).iterdir() if f.name.startswith("Задание 2.docx.bak")]
    assert b"first edit: an evening of work" in kept and b"second edit: two quick fixes" in kept
    assert "Твои версии лежат рядом" in _journal(pipe, st)


def test_students_edit_is_mentioned_once_and_kept_until_the_task_changes(tmp_home, tmp_path):
    """Пока задание у ForLabs прежнее, поправленный студентом файл не трогается; сказано об этом один раз."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st = pipe.render(_on(pipe, st))
    mine = _folder(tmp_path) / "Задание 1.docx"
    mine.write_bytes(b"student edit")
    st = pipe.render(st)
    st = pipe.render(st)
    assert mine.read_bytes() == b"student edit" and "Задание 1.docx" in st.task_docs
    assert _journal(pipe, st).count("оставил твои версии") == 1
    assert not list(_folder(tmp_path).glob("*.bak*"))  # копий не плодим: файл студента на месте


def test_no_temp_file_is_left_when_the_file_is_open_in_word(tmp_home, tmp_path, monkeypatch):
    """Файл задания открыт в Word (os.replace → PermissionError): в папке сдачи остаётся «.Задание 1.docx.xxxxxx.tmp»
    — и так при каждой вёрстке, пока файл открыт."""
    edit = {"kind": "edit", "reply": "Допишу.", "tasks": [{"n": 1, "instruction": "добавь абзац"}]}
    revised = {"blocks": [{"type": "paragraph", "text": "Совсем новый текст первого задания для проверки."}]}
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path, [edit, revised])
    st = pipe.render(_on(pipe, st))
    folder = _folder(tmp_path)
    real = os.replace

    def replace(src, dst, *a, **kw):
        if Path(dst).parent == folder and Path(dst).name.startswith("Задание 1"):
            raise PermissionError(13, "Процесс не может получить доступ к файлу", str(dst))
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", replace)
    st = pipe.chat(st, "перепиши первое задание")
    assert "открыты в Word" in _journal(pipe, st)  # студенту сказано — это работает
    left = sorted(f.name for f in folder.iterdir() if f.name.endswith(".tmp"))
    assert left == []


def test_two_works_sharing_one_folder_do_not_take_each_others_files(tmp_home, tmp_path):
    """Две работы предмета с одним номером из методички («7» и её повтор «7-2») сдаются в одну папку «ЛР7»
    (так же — два профиля с одной папкой сдачи). Имена файлов заданий у них совпадают: вторая работа заменяет
    файлы первой, а студенту говорит «эти файлы ты менял сам»; при следующей вёрстке первой — всё наоборот,
    и единственный .bak каждый раз перезаписывается."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st = pipe.render(_on(pipe, st))
    folder = _folder(tmp_path)
    name = "Ответы на контрольные вопросы.docx"
    first = (folder / name).read_bytes()
    fake.responses += [RAW, SEC, SEC2, SEC2, ANS]  # короткое задание в режиме файлов модель пишет дважды
    other = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())]), date="24.09.2026")
    assert other.number == "7-2" and other.spec.number == "7"
    other = pipe.execute(_on(pipe, other))
    other = pipe.attach_screenshot(other, section=1, block=4, image=png_bytes())
    assert other.status == "rendered", other.error
    assert Path(pipe._hand_in_dir(other)) == folder  # та же папка сдачи
    said = _journal(pipe, other)
    replaced = (folder / name).read_bytes() != first
    assert not ("ты менял сам" in said and replaced), "файл соседней работы заменён и назван правкой студента"


def test_unavailable_folder_is_not_reported_as_files_open_in_word(tmp_home, tmp_path):
    """Папка сдачи недоступна (флешку вынули; здесь — на месте папки предмета лежит файл): студенту говорят
    «файлы открыты в Word — закрой их»."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    (tmp_path / "сдача").mkdir(parents=True, exist_ok=True)
    blocker = tmp_path / "сдача" / "ПССИП"
    if blocker.exists():
        import shutil

        shutil.rmtree(blocker)
    blocker.write_bytes(b"")  # mkdir папки «ЛР7» теперь даёт OSError, как у вынутой флешки
    st = pipe.render(_on(pipe, st))
    assert st.status == "rendered", st.error  # отчёт собран — это хорошо
    assert st.task_docs == []
    assert "открыты в Word" not in _journal(pipe, st)


def test_switching_off_with_a_locked_file_does_not_forget_it(tmp_home, tmp_path, monkeypatch):
    """Режим выключают, а файл задания открыт в Word (unlink → PermissionError): файл остаётся в папке сдачи,
    ForLabs забывает, что он свой (список своих файлов пустеет), и больше никогда его не уберёт."""
    pipe, fake, st, calls = files_lab(tmp_home, tmp_path)
    st = pipe.render(_on(pipe, st))
    folder = _folder(tmp_path)
    real = Path.unlink

    def unlink(self, *a, **kw):
        if self.parent == folder and self.name == "Задание 1.docx":
            raise PermissionError(13, "Файл занят", str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", unlink)
    st = pipe.render(pipe.confirm(st, st.spec.model_copy(update={"task_files": False})))
    assert (folder / "Задание 1.docx").is_file()  # убрать не вышло — ожидаемо
    monkeypatch.setattr(Path, "unlink", real)  # студент закрыл Word
    st = pipe.render(st)
    assert not (folder / "Задание 1.docx").exists()  # при следующей вёрстке свой файл должен уйти
