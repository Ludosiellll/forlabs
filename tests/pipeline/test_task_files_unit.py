"""Режим «задания отдельными файлами»: картинки страниц и производный отчёт (без модели и без Word)."""

from PIL import Image, ImageDraw

from forlabs.core.models import QA, Figure, Listing, Paragraph, ReportDoc, TaskSection
from forlabs.pipeline import taskfiles as tf


def _page(path, text_box=(150, 120, 700, 400)):
    im = Image.new("RGB", (893, 1263), (255, 255, 255))
    ImageDraw.Draw(im).rectangle(text_box, fill=(0, 0, 0))
    im.save(path)
    return path


def test_page_is_cropped_to_its_content_with_a_margin(tmp_path):
    im = tf.crop_page(_page(tmp_path / "p.png"))
    assert im.width < 893 and im.height < 1263  # белые поля листа не тратят место в отчёте
    assert im.width >= 700 - 150 + 2 * tf.PAD - 2 and im.getpixel((2, 2)) == (255, 255, 255)
    blank = Image.new("RGB", (893, 1263), (255, 255, 255))
    blank.save(tmp_path / "b.png")
    assert tf.crop_page(tmp_path / "b.png").size == (893, 1263)  # пустая страница — как есть, без ошибки


def test_one_page_is_one_picture_and_more_go_in_pairs(tmp_path):
    pages = [_page(tmp_path / f"page-{i}.png") for i in range(1, 6)]
    one = tf.sheets(pages[:1], tmp_path / "o", "t1")
    assert [label for _, label in one] == [""] and one[0][0].is_file()
    two = tf.sheets(pages[:2], tmp_path / "w", "t1")
    assert [label for _, label in two] == [""]  # документ целиком на одном снимке — уточнять страницы незачем
    five = tf.sheets(pages, tmp_path / "f", "t1")
    assert [label for _, label in five] == ["страницы 1–2", "страницы 3–4", "страница 5"]
    pair, single = Image.open(five[0][0]), Image.open(five[2][0])
    assert pair.width > 1.8 * single.width  # две страницы рядом


def test_section_is_ready_when_nothing_is_left_for_the_student():
    done = TaskSection(n=1, title="Опишите.", blocks=[Paragraph(text="Описание."),
                                                    Figure(topic="Окно программы", image_path="assets/a.png")])
    waiting = TaskSection(n=2, title="Покажите.", blocks=[Paragraph(text="Текст."), Figure(topic="Окно", slot="Сними")])
    empty = TaskSection(n=3, title="Пусто.", blocks=[Paragraph(text="  ")])
    assert tf.ready(done) and not tf.ready(waiting) and not tf.ready(empty)


def test_presentation_tasks_stay_with_the_student():
    assert tf.is_presentation("Составьте индивидуальную презентацию «Техники сбора требований» (8 слайдов).")
    assert not tf.is_presentation("Составьте вопросы и проведите интервью.")


def test_report_shows_pictures_of_the_files_instead_of_the_text(tmp_path):
    sec1 = TaskSection(n=1, title="Составьте вопросы.", doc_title="Вопросы интервью по требованиям к приложению",
                       blocks=[Paragraph(text="Вопросы составлены."), Listing(topic="код", code="x = 1")])
    sec2 = TaskSection(n=2, title="Покажите окно.", blocks=[Figure(topic="Окно", slot="Сними")])
    doc = ReportDoc(lab_number="2", sections=[sec1, sec2], conclusion="Изучила.",
                    questions=[QA(n=1, question="Что это?", answer="Это требование.")])
    shots = {1: [(tmp_path / "a.png", "страницы 1–2"), (tmp_path / "b.png", "страница 3")],
             tf.ANSWERS: [(tmp_path / "q.png", "")]}
    out = tf.as_screens(doc, shots)
    figs = out.sections[0].blocks
    assert [type(b).__name__ for b in figs] == ["Figure", "Figure"]
    assert figs[0].topic == "Вопросы интервью по требованиям к приложению (страницы 1–2)"
    assert figs[0].image_path == str(tmp_path / "a.png") and figs[1].topic.endswith(" (страница 3)")
    assert out.sections[1] == sec2  # задание, которое ждёт снимка студента, остаётся как было
    assert out.questions == [] and [f.topic for f in out.answer_figures] == ["Ответы на контрольные вопросы"]
    assert doc.sections[0].blocks[0].text == "Вопросы составлены." and doc.questions  # исходный отчёт не тронут
    # без названия документа подпись всё равно осмысленная
    plain = tf.as_screens(ReportDoc(lab_number="2", sections=[sec1.model_copy(update={"doc_title": ""})]),
                          {1: [(tmp_path / "a.png", "")]})
    assert plain.sections[0].blocks[0].topic == "Результат выполнения задания 1"


def test_file_names_are_safe_and_stable():
    sec = TaskSection(n=3, title="Задание", doc_title='ТЗ на приложение «Макет 3D»: требования/состав?')
    assert tf.file_name(sec) == "Задание 3 — ТЗ на приложение «Макет 3D» требования состав.docx"
    assert tf.file_name(TaskSection(n=1, title="Задание")) == "Задание 1.docx"
    long = TaskSection(n=2, title="Задание", doc_title="Очень " * 40)
    assert len(tf.file_name(long)) <= 90 and tf.file_name(long).endswith(".docx")
