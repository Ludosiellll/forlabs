"""Файлы к лабе (выдал преподаватель): модель читает их целиком в каждом задании и в чате, но объём ограничен."""

import zipfile

from test_run import ANS, FIXTURE, RAW, SEC, SEC2, setup

from forlabs.pipeline.materials import MATERIALS_MAX, PAGE_CHARS, read_material


def _xlsx(path, rows):
    shared = sorted({v for r in rows for v in r if isinstance(v, str)})
    cells = []
    for i, row in enumerate(rows, start=1):
        cs = []
        for j, v in enumerate(row):
            ref = f"{'ABCDEFGH'[j]}{i}"
            if isinstance(v, str):
                cs.append(f'<c r="{ref}" t="s"><v>{shared.index(v)}</v></c>')
            else:
                cs.append(f'<c r="{ref}"><v>{v}</v></c>')
        cells.append(f'<row r="{i}">{"".join(cs)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rel}><sheets><sheet name="Оценки" sheetId="1" r:id="rId1"/>'
                                      "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml" '
                   'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
                   "</Relationships>")
        z.writestr("xl/sharedStrings.xml", f"<sst {ns}>" + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{''.join(cells)}</sheetData></worksheet>")
    return path


def test_read_material_keeps_code_and_reads_tables(tmp_path):
    code = tmp_path / "Program.cs"
    code.write_text("class P\n{\n    static void Main()\n    {\n    }\n}\n", encoding="utf-8")
    assert "    static void Main()" in read_material(code)  # отступы кода не съедаются
    csv = tmp_path / "данные.csv"
    csv.write_text("ФИО;Оценка\nИванов;5\n", encoding="cp1251")
    assert "Иванов;5" in read_material(csv)
    table = read_material(_xlsx(tmp_path / "оценки.xlsx", [["ФИО", "Оценка"], ["Петров", 4]]))
    assert "Оценки" in table and "Петров\t4" in table
    bad = tmp_path / "старый.doc"
    bad.write_bytes(b"\xd0\xcf\x11\xe0")
    try:
        read_material(bad)
    except ValueError as e:
        assert "DOCX" in str(e)
    else:
        raise AssertionError("формат .doc должен отклоняться")


def test_materials_reach_every_task_and_chat(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st, problems = pipe.add_materials(st, [("студенты.txt", "Иванов;5;4\nПетров;3;5\n".encode())])
    assert problems == [] and [m.name for m in st.materials] == ["студенты.txt"]
    st = pipe.parse(st, date="24.09.2026")
    before = len(fake.calls)
    st = pipe.execute(pipe.confirm(st, None))
    writes = [call for call in fake.calls[before:] if "Петров;3;5" in "\n".join(m["content"] for m in call)]
    assert len(writes) == 2  # оба задания видят файл целиком (ответы на теорию — без него)
    fake.responses[:] = [{"kind": "edit", "reply": "Поправлю.", "tasks": [{"n": 1, "instruction": "возьми данные из файла"}]},
                         {"blocks": [{"keep": "b1"}]}]
    before = len(fake.calls)
    st = pipe.chat(st, "возьми оценки из приложенного файла")
    plan, revise = ("\n".join(m["content"] for m in call) for call in fake.calls[before:before + 2])
    assert "студенты.txt" in plan  # планировщик знает про файл, но не читает его зря
    assert "Петров;3;5" in revise  # правка задания видит файл целиком
    st = pipe.remove_material(st, "студенты.txt")
    assert st.materials == [] and "Петров;3;5" not in pipe._memory_text(st)


def test_materials_limit_keeps_small_rejects_huge(tmp_home, tmp_path):
    pipe, _ = setup(tmp_home, tmp_path, [RAW])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    huge = ("Текст справочника. " * 30 + "\n") * 700  # ≈ 200 страниц Word
    st, problems = pipe.add_materials(st, [("справочник.txt", huge.encode()), ("задание.txt", "Данные: 1 2 3".encode()),
                                           ("пустой.txt", b"   ")])
    assert [m.name for m in st.materials] == ["задание.txt"]
    big = next(p for p in problems if "справочник.txt" in p)
    assert f"до {MATERIALS_MAX // PAGE_CHARS} стр" in big and "нужные страницы" in big
    assert any("пустой.txt" in p and "текста" in p for p in problems)
    # повторная загрузка того же файла заменяет его, а не дублирует
    st, _ = pipe.add_materials(st, [("задание.txt", "Данные: 4 5 6".encode())])
    assert len(st.materials) == 1 and "4 5 6" in st.materials[0].text
    reloaded = pipe.store.load_lab("ivanov", "psip", "7")
    assert reloaded.materials[0].text == st.materials[0].text  # сохраняется с лабой


def _xlsx_raw(path, sheet_xml, styles=None, shared=None):
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rel}><sheets><sheet name="Лист1" sheetId="1" r:id="rId1"/>'
                                      "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="/xl/worksheets/sheet1.xml" '
                   'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
                   "</Relationships>")
        if styles:
            z.writestr("xl/styles.xml", f"<styleSheet {ns}>{styles}</styleSheet>")
        if shared is not None:
            z.writestr("xl/sharedStrings.xml", shared)
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{sheet_xml}</sheetData></worksheet>")
    return path


def test_xlsx_cells_without_address_dates_and_inline_strings(tmp_path):
    styles = ('<numFmts count="1"><numFmt numFmtId="164" formatCode="dd/mm/yyyy;@"/></numFmts>'
              '<cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/></cellXfs>')
    sheet = ('<row><c t="inlineStr"><is><t>x</t></is></c><c t="inlineStr"><is><t>y</t></is></c><c><v>3</v></c></row>'
             '<row r="2"><c r="A2" s="1"><v>45962</v></c><c r="C2" s="2"><v>45963.5</v></c><c r="D2"><v>2.50</v></c></row>')
    text = read_material(_xlsx_raw(tmp_path / "t.xlsx", sheet, styles))
    assert "x\ty\t3" in text  # без адресов ячейки идут подряд, а не в одну
    assert "01.11.2025\t\t02.11.2025\t2.50" in text  # даты — датами, а не числами 45962


def test_huge_xlsx_rejected_without_reading_it_all(tmp_path):
    import time

    row = "".join(f'<c r="{c}{{i}}" t="inlineStr"><is><t>значение-{c}</t></is></c>' for c in "ABCDEF")
    sheet = "".join(f'<row r="{i}">' + row.replace("{i}", str(i)) + "</row>" for i in range(1, 60_000))
    path = _xlsx_raw(tmp_path / "большая.xlsx", sheet)
    t = time.monotonic()
    try:
        read_material(path)
    except ValueError as e:
        assert "строк" in str(e) or "лист" in str(e)
    else:
        raise AssertionError("огромная таблица должна отклоняться")
    assert time.monotonic() - t < 3  # читает до лимита и останавливается
