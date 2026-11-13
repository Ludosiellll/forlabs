"""Чат правок в конвейере: правка, вопрос, память требований, сбой, откат."""

import zipfile
from pathlib import Path

import pytest
from test_run import ANS, FIXTURE, RAW, SEC, SEC2, png_bytes, setup

from forlabs.core.llm import LLMError
from forlabs.pipeline.memory import SubjectMemory
from forlabs.pipeline.run import PipelineError


def ready(tmp_home, tmp_path, extra):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS] + extra)
    pipe.memory = SubjectMemory()
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    assert st.status == "rendered", st.error
    return pipe, fake, st


PLAN_T2 = {"kind": "edit", "reply": "Допишу во втором задании про прокрутку.",
           "tasks": [{"n": 2, "instruction": "добавь абзац о том, как фон движется при прокрутке колесом мыши"}]}
REVISE_T2 = {"blocks": [{"keep": "b1"},
                        {"type": "paragraph", "text": "При прокрутке колесом мыши фон сдвигается вдвое медленнее текста."}]}


def test_edit_changes_only_the_named_task(tmp_home, tmp_path):
    pipe, fake, st = ready(tmp_home, tmp_path, [PLAN_T2, REVISE_T2])
    before1 = st.report.sections[0].model_dump()
    st = pipe.chat(st, "Преподаватель сказал дописать во втором задании про прокрутку")
    assert st.report.sections[0].model_dump() == before1
    assert [b.text for b in st.report.sections[1].blocks][-1].startswith("При прокрутке")
    assert [m.role for m in st.chat] == ["user", "assistant"]
    last = st.chat[-1]
    assert last.kind == "edit" and last.changed == ["задание 2"] and last.version == 1 and len(st.versions) == 1
    assert st.status == "rendered", st.error
    xml = zipfile.ZipFile(st.docx_path).read("word/document.xml").decode()
    assert "вдвое медленнее" in xml
    # сообщение чата помечено: пробный доступ на сервере считает сообщения правок по лабе
    assert fake.headers == {"X-ForLabs-Lab": "ivanov%2Fpsip%2F7", "X-ForLabs-Chat": st.chat[0].id}


def test_keep_preserves_relative_image_path(tmp_home, tmp_path):
    plan = {"kind": "edit", "reply": "Поправлю первый абзац.", "tasks": [{"n": 1, "instruction": "короче"}]}
    revise = {"blocks": [{"type": "paragraph", "text": "В файле style.css описаны две анимации:"},
                         {"keep": "b2"}, {"keep": "b3"}, {"keep": "b4"}]}
    pipe, fake, st = ready(tmp_home, tmp_path, [plan, revise])
    path = st.report.sections[0].blocks[3].image_path
    assert path.startswith("assets/fig_1_4_") and not Path(path).is_absolute()
    st = pipe.chat(st, "первый абзац короче")
    fig = st.report.sections[0].blocks[3]
    assert fig.image_path == path and not pipe.slots(st)
    assert zipfile.ZipFile(st.docx_path).read("word/document.xml").decode().count("<pic:pic") == 1


def test_variant_and_conclusion(tmp_home, tmp_path):
    plan = {"kind": "edit", "reply": "Поменяю вариант и вывод.", "variant": 7,
            "variant_text": "Вариант 7: страница о Гомеле", "conclusion": "Научился делать параллакс на странице"}
    pipe, fake, st = ready(tmp_home, tmp_path, [plan])
    st = pipe.chat(st, "вариант 7, и вывод другой")
    assert st.spec.variant == 7 and st.spec.variant_text == "Вариант 7: страница о Гомеле"
    assert st.report.conclusion == "Научился делать параллакс на странице."
    assert set(st.chat[-1].changed) == {"вариант 7", "текст варианта", "вывод"}


def test_question_does_not_touch_report(tmp_home, tmp_path):
    pipe, fake, st = ready(tmp_home, tmp_path, [{"kind": "answer", "reply": "Так короче и понятнее."}])
    report = st.report.model_dump()
    st = pipe.chat(st, "почему анимации две?")
    assert st.report.model_dump() == report and st.versions == []
    assert st.chat[-1].kind == "answer" and st.chat[-1].text == "Так короче и понятнее."


def test_remember_goes_to_teacher_memory_and_next_prompt(tmp_home, tmp_path):
    plan1 = {"kind": "answer", "reply": "Хорошо, учту.", "remember": "Код с комментариями к каждому правилу CSS"}
    pipe, fake, st = ready(tmp_home, tmp_path, [plan1, {"kind": "answer", "reply": "Ок."}])
    st = pipe.chat(st, "преподаватель всегда хочет комментарии в коде", remember=True)
    assert "Код с комментариями" in pipe.memory.teacher("ivanov", "psip")
    assert "Запомнил" in st.chat[-1].text
    pipe.chat(st, "а ещё что-нибудь?")
    assert "Код с комментариями" in fake.calls[-1][-1]["content"]
    assert "Код с комментариями" in pipe._memory_text(st)


def test_failure_restores_report(tmp_home, tmp_path):
    pipe, fake, st = ready(tmp_home, tmp_path, [PLAN_T2, LLMError("network", "нет сети")])
    report, spec = st.report.model_dump(), st.spec.model_dump()
    st = pipe.chat(st, "дописать про прокрутку")
    assert st.report.model_dump() == report and st.spec.model_dump() == spec
    assert st.versions == [] and st.chat[-1].kind == "failed" and "не изменён" in st.chat[-1].text
    saved = pipe.store.load_lab("ivanov", "psip", "7")
    assert saved.chat[-1].kind == "failed"


def test_empty_message_refused(tmp_home, tmp_path):
    pipe, fake, st = ready(tmp_home, tmp_path, [])
    with pytest.raises(PipelineError):
        pipe.chat(st, "   ")
    assert st.chat == []


def test_revert_and_unknown_version(tmp_home, tmp_path):
    pipe, fake, st = ready(tmp_home, tmp_path, [PLAN_T2, REVISE_T2])
    before = st.report.sections[1].model_dump()
    st = pipe.chat(st, "дописать про прокрутку")
    with pytest.raises(PipelineError):
        pipe.revert(st, 99)
    st = pipe.revert(st, 1)
    assert st.report.sections[1].model_dump() == before
    assert st.chat[-1].kind == "revert" and st.chat[-1].version == 2  # откат тоже можно отменить
    assert st.status == "rendered", st.error


def test_wishes_reach_parse_write_and_chat(tmp_home, tmp_path):
    from test_run import setup as base_setup

    pipe, fake = base_setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS, {"kind": "answer", "reply": "Ок."}])
    wishes = "Тема сайта — Гомель, а не Бобруйск; анимации делать на чистом CSS"
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7", wishes=wishes)
    st = pipe.parse(st, date="24.09.2026")
    assert st.spec.wishes == wishes
    assert wishes in fake.calls[0][-1]["content"]  # разбор
    st = pipe.execute(pipe.confirm(st, None))
    assert wishes in fake.calls[1][-1]["content"]  # написание задания
    pipe.chat(st, "почему так?")
    assert wishes in fake.calls[-1][-1]["content"]  # чат правок


def test_wish_photo_is_read_before_parse(tmp_home, tmp_path):
    from test_images import png
    from test_run import setup as base_setup

    pipe, fake = base_setup(tmp_home, tmp_path, ["Фото 1: Преподаватель: вариант 7, анимации только на CSS.", RAW])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7",
                         wishes="см. фото", wish_images=[("доска.png", png(800, 600))])
    assert st.wish_images == ["notes/wish_1.jpg"]
    st = pipe.parse(st, date="24.09.2026")
    assert "вариант 7, анимации только на CSS" in st.spec.wishes and st.spec.wishes.startswith("см. фото")
    assert "вариант 7, анимации только на CSS" in fake.calls[1][-1]["content"]  # разбор видит текст с фото


def test_chat_photo_without_text(tmp_home, tmp_path):
    from test_images import png

    pipe, fake, st = ready(tmp_home, tmp_path, ["Фото 1: на скриншоте ошибка в строке 12.",
                                                {"kind": "answer", "reply": "Ошибка в строке 12 из-за опечатки."}])
    st = pipe.chat(st, "", images=[("err.png", png(600, 400))])
    user = st.chat[-2]
    assert user.images == [f"notes/chat_{user.id}_1.jpg"] and "строке 12" in user.image_text
    assert (pipe.lab_dir(st) / user.images[0]).exists()
    assert "на скриншоте ошибка в строке 12" in fake.calls[-1][-1]["content"]


def test_too_many_photos_refused(tmp_home, tmp_path):
    from test_images import png

    pipe, fake, st = ready(tmp_home, tmp_path, [])
    with pytest.raises(PipelineError):
        pipe.chat(st, "смотри", images=[(f"{i}.png", png(100, 100)) for i in range(5)])
    with pytest.raises(PipelineError):
        pipe.chat(st, "смотри", images=[("x.png", b"not an image")])
    assert st.chat == []


def _old_document_with_squares(pipe, st):
    """Документ, свёрстанный версией до 1.0.17: у абзацев стоят настройки «не отрывать от следующего»."""
    import shutil

    from forlabs.pipeline.run import file_sha
    from forlabs.render import freeze

    docx = Path(st.docx_path)
    with zipfile.ZipFile(docx) as z:
        infos = z.infolist()
        data = {i.filename: z.read(i.filename) for i in infos}
    data["word/document.xml"] = data["word/document.xml"].decode().replace("<w:pPr>", "<w:pPr><w:keepNext/>",
                                                                           5).encode()
    tmp = docx.with_name("old.docx")
    with zipfile.ZipFile(tmp, "w") as z:
        for i in infos:
            z.writestr(i, data[i.filename])
    shutil.move(tmp, docx)
    st.docx_sha = file_sha(docx)
    assert freeze.flags_left(docx) > 0
    return docx


def test_request_to_remove_squares_rebuilds_and_says_what_really_happened(tmp_home, tmp_path):
    from forlabs.render import freeze

    pipe, fake, st = ready(tmp_home, tmp_path, [{"kind": "answer", "reply": "Сейчас посмотрю."}])
    docx = _old_document_with_squares(pipe, st)
    st = pipe.chat(st, "убери квадраты слева, невидимые символы по всему тексту")
    assert freeze.flags_left(docx) == 0 and st.status == "rendered", st.error
    assert "квадрат" in fake.calls[-1][0]["content"].lower()  # чат знает, что это не текст отчёта
    text = st.chat[-1].text
    assert "не осталось" in text and st.versions == []


def test_submitting_an_old_document_hands_in_one_without_squares(tmp_home, tmp_path):
    from forlabs.pipeline.run import docx_fresh
    from forlabs.render import freeze

    pipe, fake, st = ready(tmp_home, tmp_path, [])
    docx = _old_document_with_squares(pipe, st)
    st = pipe.mark_final(st)
    handed = list((tmp_path / "сдача").rglob("*.docx"))
    assert handed and all(freeze.flags_left(f) == 0 for f in handed)
    assert freeze.flags_left(docx) == 0 and docx_fresh(st, pipe.lab_dir(st))
