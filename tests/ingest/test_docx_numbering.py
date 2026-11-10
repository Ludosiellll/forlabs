"""Автонумерация Word: номер пункта есть на экране, но не в тексте абзаца. Без неё пропадают номера вариантов в
таблицах, номера шагов задания и вопросов."""

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

from forlabs.ingest.extract import extract_text


def add_list(d, fmt="decimal", text="%1.", start=1, levels=1) -> int:
    """Новый список в документе; возвращает его numId."""
    numbering = d.part.numbering_part.element
    aid = max([int(x.get(qn("w:abstractNumId"))) for x in numbering.findall(qn("w:abstractNum"))], default=0) + 1
    lvls = "".join(
        f'<w:lvl w:ilvl="{i}"><w:start w:val="{start}"/><w:numFmt w:val="{fmt}"/>'
        f'<w:lvlText w:val="{text if i == 0 else "%1.%2."}"/></w:lvl>' for i in range(levels))
    abstract = parse_xml(f'<w:abstractNum {nsdecls("w")} w:abstractNumId="{aid}">{lvls}</w:abstractNum>')
    first = numbering.find(qn("w:num"))
    first.addprevious(abstract) if first is not None else numbering.append(abstract)
    nid = max([int(x.get(qn("w:numId"))) for x in numbering.findall(qn("w:num"))], default=0) + 1
    numbering.append(parse_xml(f'<w:num {nsdecls("w")} w:numId="{nid}"><w:abstractNumId w:val="{aid}"/></w:num>'))
    return nid


def numbered(p, nid, lvl=0):
    p._p.get_or_add_pPr().append(
        parse_xml(f'<w:numPr {nsdecls("w")}><w:ilvl w:val="{lvl}"/><w:numId w:val="{nid}"/></w:numPr>'))
    return p


def lines_of(d, tmp_path):
    f = tmp_path / "m.docx"
    d.save(str(f))
    return extract_text(f).text.split("\n")


def test_numbered_paragraphs_get_their_numbers(tmp_path):
    d = Document()
    nid = add_list(d)
    d.add_paragraph("Порядок выполнения работы")
    for t in ("Установите JDK", "Проверьте работу утилит javac и java.", "Установите Eclipse."):
        numbered(d.add_paragraph(t), nid)
    assert lines_of(d, tmp_path) == ["Порядок выполнения работы", "1. Установите JDK",
                                     "2. Проверьте работу утилит javac и java.", "3. Установите Eclipse."]


def test_variant_numbers_in_empty_table_cells(tmp_path):
    # ЛР 3 по Java: «№ варианта» — пустые ячейки с автонумерацией
    d = Document()
    nid = add_list(d)
    t = d.add_table(rows=4, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "№ варианта", "Условие задания"
    for i, text in enumerate(["Проверьте число на чётность.", "Найдите максимум.", "Дано 3 числа."], start=1):
        numbered(t.cell(i, 0).paragraphs[0], nid)
        t.cell(i, 1).text = text
    assert lines_of(d, tmp_path) == ["№ варианта | Условие задания", "1. | Проверьте число на чётность.",
                                     "2. | Найдите максимум.", "3. | Дано 3 числа."]


def test_two_lists_count_separately_and_one_list_continues(tmp_path):
    d = Document()
    a, b = add_list(d), add_list(d, text="%1)")
    numbered(d.add_paragraph("первый"), a)
    numbered(d.add_paragraph("вопрос один"), b)
    d.add_paragraph("обычный абзац")
    numbered(d.add_paragraph("второй"), a)
    numbered(d.add_paragraph("вопрос два"), b)
    assert lines_of(d, tmp_path) == ["1. первый", "1) вопрос один", "обычный абзац", "2. второй", "2) вопрос два"]


def test_bullets_letters_and_start(tmp_path):
    d = Document()
    bullet, letters, from5 = add_list(d, fmt="bullet", text=""), add_list(d, fmt="russianLower", text="%1)"), \
        add_list(d, start=5)
    numbered(d.add_paragraph("пункт"), bullet)
    numbered(d.add_paragraph("первый"), letters)
    numbered(d.add_paragraph("второй"), letters)
    numbered(d.add_paragraph("пятый"), from5)
    assert lines_of(d, tmp_path) == ["- пункт", "а) первый", "б) второй", "5. пятый"]


def test_nested_levels(tmp_path):
    d = Document()
    nid = add_list(d, levels=2)
    numbered(d.add_paragraph("Создать базу"), nid)
    numbered(d.add_paragraph("таблицу users"), nid, 1)
    numbered(d.add_paragraph("таблицу posts"), nid, 1)
    numbered(d.add_paragraph("Написать запросы"), nid)
    numbered(d.add_paragraph("простые"), nid, 1)
    assert lines_of(d, tmp_path) == ["1. Создать базу", "1.1. таблицу users", "1.2. таблицу posts",
                                     "2. Написать запросы", "2.1. простые"]


def test_numbering_from_the_paragraph_style(tmp_path):
    d = Document()
    d.add_paragraph("Что такое класс?", style="List Number")
    d.add_paragraph("Что такое объект?", style="List Number")
    assert lines_of(d, tmp_path) == ["1. Что такое класс?", "2. Что такое объект?"]


def test_number_already_typed_by_hand_is_not_doubled(tmp_path):
    d = Document()
    nid = add_list(d)
    numbered(d.add_paragraph("1. Создать базу данных"), nid)
    assert lines_of(d, tmp_path) == ["1. Создать базу данных"]


def test_numbering_switched_off_and_empty_items(tmp_path):
    d = Document()
    nid = add_list(d)
    numbered(d.add_paragraph("без номера"), 0)  # numId 0 — нумерация снята
    numbered(d.add_paragraph("первый"), nid)
    numbered(d.add_paragraph(""), nid)  # пустой пункт: Word считает его, строки в тексте нет
    numbered(d.add_paragraph("третий"), nid)
    assert [ln for ln in lines_of(d, tmp_path) if ln] == ["без номера", "1. первый", "3. третий"]
