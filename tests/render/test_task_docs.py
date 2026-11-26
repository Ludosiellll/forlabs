"""Документ задания: то же содержимое, что в отчёте, отдельным файлом Word — с меткой студента и без рамки отчёта."""

import io
import zipfile

from PIL import Image

from forlabs.core.models import QA, Paragraph, Table, TaskSection
from forlabs.render.docx_builder import build_answers_doc, build_lab, build_task_doc


def _stamp() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (120, 120), (200, 80, 80)).save(buf, "PNG")
    return buf.getvalue()


def _texts(docx) -> list[str]:
    import docx as docx_lib

    return [p.text for p in docx_lib.Document(str(docx)).paragraphs if p.text.strip()]


def _xml(docx, name="word/document.xml") -> str:
    with zipfile.ZipFile(docx) as z:
        return z.read(name).decode("utf-8")


def test_task_document_has_the_content_of_the_task(sample_doc, tmp_path):
    section = sample_doc.sections[0].model_copy(update={"doc_title": "Программа вывода лесенки"})
    out = build_task_doc(section, tmp_path / "Задание 1.docx", author="Иванова Анна")
    texts = _texts(out)
    assert texts[0] == "Программа вывода лесенки"  # заголовок документа, а не «Лабораторная работа №8»
    assert not any(t.startswith(("Лабораторная работа", "Номер учебной группы", "Результаты выполнения")) for t in texts)
    assert "Листинг 1 – Исходный код программы вывода лесенки" in texts  # нумерация — своя, с единицы
    report = _texts(build_lab(sample_doc, tmp_path / "lab.docx", author="Иванова Анна"))
    body = [t for t in texts[1:]]
    assert body and all(t in report for t in body)  # те же абзацы, что в отчёте


def test_task_document_without_a_title_is_named_by_the_task(tmp_path):
    section = TaskSection(n=3, title="Составьте вопросы интервью.", blocks=[Paragraph(text="Вопросы составлены.")])
    assert _texts(build_task_doc(section, tmp_path / "t.docx", author="А"))[0] == "Задание 3. Составьте вопросы интервью."


def test_task_document_has_no_report_frame_and_keeps_the_page_setup(sample_doc, tmp_path):
    out = build_task_doc(sample_doc.sections[0], tmp_path / "t.docx", author="А")
    xml = _xml(out)
    assert "headerReference" not in xml and "footerReference" not in xml  # рамка отчёта живёт в колонтитуле
    assert 'w:w="11906"' in xml and "w:pgMar" in xml  # лист А4 и поля — как у отчёта
    assert "headerReference" in _xml(build_lab(sample_doc, tmp_path / "lab.docx", author="А"))


def test_stamp_is_the_first_thing_on_the_first_page(sample_doc, tmp_path):
    out = build_task_doc(sample_doc.sections[0], tmp_path / "t.docx", author="А", stamp=_stamp())
    xml = _xml(out)
    body = xml[xml.index("<w:body"):]
    assert "<w:drawing" in body and body.index("<w:drawing") < body.index("<w:t")  # картинка — раньше любого текста
    assert 'cx="936000"' in body and 'cy="936000"' in body  # 2,6 см: как у образца, квадрат не искажается
    plain = build_task_doc(sample_doc.sections[0], tmp_path / "p.docx", author="А")
    first = _xml(plain)
    assert first.index("<w:t") < (first.index("<w:drawing") if "<w:drawing" in first else len(first))


def test_answers_document(tmp_path):
    qs = [QA(n=1, question="Что такое требование?", answer="Условие, нужное пользователю."),
          QA(n=2, question="Как выявляют требования?", answer="Ответ: Интервью и анкеты.")]
    out = build_answers_doc(qs, tmp_path / "Ответы.docx", author="А", stamp=_stamp())
    texts = _texts(out)
    assert texts == ["Ответы на контрольные вопросы", "1. Что такое требование?", "Ответ: Условие, нужное пользователю.",
                     "2. Как выявляют требования?", "Ответ: Интервью и анкеты."]
    assert "<w:drawing" in _xml(out)


def test_table_in_a_task_document(tmp_path):
    section = TaskSection(n=1, title="Опишите компоненты.", doc_title="Компоненты модели", blocks=[
        Table(topic="компоненты модели", rows=[["Компонент", "Назначение"], ["Проект", "Хранит комнаты"]])])
    texts = _texts(build_task_doc(section, tmp_path / "t.docx", author="А"))
    assert texts[:2] == ["Компоненты модели", "В таблице 1 представлены компоненты модели"]
