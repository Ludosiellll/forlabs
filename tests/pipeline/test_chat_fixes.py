"""Находки ревью чата правок: каждая воспроизведена тестом."""

import hashlib
import io

import pytest
from PIL import Image
from test_chat_run import PLAN_T2, REVISE_T2, ready
from test_run import FIXTURE, RAW, SEC, setup

from forlabs.core.llm import LLMError
from forlabs.pipeline.run import PipelineError


def other_png(color=(10, 120, 200)):
    buf = io.BytesIO()
    Image.new("RGB", (900, 500), color).save(buf, format="PNG")
    return buf.getvalue()


def sha(path):
    return hashlib.sha1(path.read_bytes()).hexdigest()


PLAN_T1 = {"kind": "edit", "reply": "Поправлю первое задание.", "tasks": [{"n": 1, "instruction": "…"}]}


def test_new_screenshot_never_overwrites_a_kept_one(tmp_home, tmp_path):
    revise = {"blocks": [{"keep": "b1"}, {"keep": "b2"}, {"keep": "b3"},
                         {"type": "figure", "kind": "screenshot", "topic": "Анимация меню", "instruction": "Наведи на меню"},
                         {"keep": "b4"}]}
    pipe, fake, st = ready(tmp_home, tmp_path, [PLAN_T1, revise])
    kept = st.report.sections[0].blocks[3].image_path
    before = sha(pipe.lab_dir(st) / kept)
    st = pipe.chat(st, "добавь скриншот меню")
    st = pipe.attach_screenshot(st, section=1, block=4, image=other_png())
    new_fig, kept_fig = st.report.sections[0].blocks[3], st.report.sections[0].blocks[4]
    assert kept_fig.image_path == kept and new_fig.image_path != kept
    assert sha(pipe.lab_dir(st) / kept) == before


def test_retake_then_revert_restores_old_screenshot(tmp_home, tmp_path):
    revise = {"blocks": [{"keep": "b1"}, {"keep": "b2"}, {"keep": "b3"}, {"keep": "b4", "retake": True}]}
    pipe, fake, st = ready(tmp_home, tmp_path, [PLAN_T1, revise])
    old = st.report.sections[0].blocks[3].image_path
    old_sha = sha(pipe.lab_dir(st) / old)
    st = pipe.chat(st, "поменялся вывод")
    st = pipe.attach_screenshot(st, section=1, block=4, image=other_png())
    st = pipe.revert(st, 1)
    fig = st.report.sections[0].blocks[3]
    assert fig.image_path == old and sha(pipe.lab_dir(st) / old) == old_sha


def test_chat_refused_on_incomplete_lab(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, LLMError("network", "нет сети")])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    assert st.status == "failed" and len(st.report.sections) == 1
    with pytest.raises(PipelineError) as e:
        pipe.chat(st, "вывод другой")
    assert "Продолжить" in str(e.value)


def test_reply_tells_when_document_did_not_build(tmp_home, tmp_path, monkeypatch):
    import forlabs.pipeline.run as run_mod

    pipe, fake, st = ready(tmp_home, tmp_path, [PLAN_T2, REVISE_T2])

    def locked(*a, **k):
        raise PermissionError(13, "Permission denied", "report.docx")

    monkeypatch.setattr(run_mod, "build_lab", locked)
    st = pipe.chat(st, "дописать про прокрутку")
    assert "не собрался" in st.chat[-1].text and "открыт в Word" in st.error
    saved = pipe.store.load_lab("ivanov", "psip", "7")
    assert saved.report.sections[1].blocks[-1].text.startswith("При прокрутке")  # правка сохранена


def test_versions_do_not_carry_methodology_text(tmp_home, tmp_path):
    pipe, fake, st = ready(tmp_home, tmp_path, [PLAN_T2, REVISE_T2])
    source = st.spec.source_text
    st = pipe.chat(st, "дописать про прокрутку")
    assert st.versions[0].spec.source_text == ""
    st = pipe.revert(st, 1)
    assert st.spec.source_text == source and source


def test_remember_needs_a_formulated_requirement(tmp_home, tmp_path):
    pipe, fake, st = ready(tmp_home, tmp_path, [{"kind": "answer", "reply": "Хорошо."}])
    st = pipe.chat(st, "во втором задании добавь проверку", remember=True)
    assert pipe.memory.teacher("ivanov", "psip") == ""
    assert "Не понял, что запомнить" in st.chat[-1].text


def test_remember_only_after_successful_edit(tmp_home, tmp_path):
    plan = dict(PLAN_T2, remember="Всегда описывать прокрутку")
    pipe, fake, st = ready(tmp_home, tmp_path, [plan, LLMError("network", "нет сети")])
    st = pipe.chat(st, "дописать про прокрутку и запомни", remember=True)
    assert st.chat[-1].kind == "failed" and pipe.memory.teacher("ivanov", "psip") == ""


def test_teacher_requirements_can_be_removed(tmp_home, tmp_path):
    from forlabs.pipeline.memory import SubjectMemory

    mem = SubjectMemory()
    mem.add_teacher("ivanov", "psip", "Комментарии к каждому методу")
    mem.add_teacher("ivanov", "psip", "Скриншоты на весь экран")
    assert mem.teacher_list("ivanov", "psip") == ["Комментарии к каждому методу.", "Скриншоты на весь экран."]
    mem.remove_teacher("ivanov", "psip", 0)
    assert mem.teacher_list("ivanov", "psip") == ["Скриншоты на весь экран."]


def test_edit_of_submitted_lab_reminds_to_submit_again(tmp_home, tmp_path):
    pipe, fake, st = ready(tmp_home, tmp_path, ["Итог ЛР 7 для памяти предмета.", PLAN_T2, REVISE_T2])
    st = pipe.mark_final(st)  # сдача обновляет память предмета — это первый ответ модели
    st = pipe.chat(st, "дописать про прокрутку")
    assert "Сдай заново" in st.chat[-1].text


def test_wish_photos_follow_the_renumbered_lab(tmp_home, tmp_path):
    from test_images import png

    pipe, fake = setup(tmp_home, tmp_path, ["Фото 1: вариант 7.", RAW])
    uploads = []
    pipe.store.upload = lambda local, p, s, n, rel: uploads.append((n, rel))
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], wish_images=[("a.png", png(300, 200))])
    pipe.parse(st, date="24.09.2026")
    assert ("7", "notes/wish_1.jpg") in uploads


def test_unreadable_wish_photo_does_not_block_parse(tmp_home, tmp_path):
    from test_images import png

    pipe, fake = setup(tmp_home, tmp_path, [LLMError("bad_response", "картинки не поддерживаются"), RAW])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7",
                         wish_images=[("a.png", png(300, 200))])
    st = pipe.parse(st, date="24.09.2026")
    assert st.status == "parsed", st.error
