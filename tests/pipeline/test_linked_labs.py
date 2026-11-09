"""Связь лаб предмета: тематика (не вытесняется), ссылки методички на прошлые лабы («из ЛР №1»)."""

from test_run import ANS, FIXTURE, RAW, SEC, SEC2, setup

from forlabs.pipeline.memory import SubjectMemory, referenced_labs


def test_referenced_labs_finds_numbers_and_previous():
    assert referenced_labs("Используйте приложение из лабораторной работы №1.", "30") == ["1"]
    assert referenced_labs("Продолжите ЛР 5.2 и л/р №3", "7") == ["5.2", "3"]
    assert referenced_labs("Лабораторная работа №7. Тема: …", "7") == []
    assert referenced_labs("Доработайте сайт из предыдущей лабораторной работы", "7") == ["previous"]
    assert referenced_labs("Обычный текст без ссылок", "7") == []


def test_theme_is_kept_apart_and_not_evicted(tmp_home):
    mem = SubjectMemory()
    from forlabs.core.models import LabSpec, LabState, ReportDoc

    def lab(n):
        spec = LabSpec(subject_id="psip", number=n, date="01.01.2026", topic=f"Тема {n}", goal="ц")
        return LabState(profile_id="p", subject_id="psip", number=n, spec=spec,
                        report=ReportDoc(lab_number=n, header={}, conclusion="в"))

    class L:
        def __init__(self, text):
            self.text = text

        def chat(self, messages, temperature=None):
            return self.text

    mem.update(lab("1"), L("Тематика: сайт кофейни «Зерно»\nГлавная страница и меню, файлы index.html, style.css."))
    for i in range(2, 40):
        mem.update(lab(str(i)), L("Тематика: что-то другое\n" + "сделано много всего; " * 30))
    assert mem.theme("p", "psip") == "сайт кофейни «Зерно»"  # первая тематика не перезаписана и не вытеснена
    assert "Тематика:" not in mem.read("p", "psip")
    mem.set_theme("p", "psip", "сайт пекарни «Колос»")
    assert mem.theme("p", "psip") == "сайт пекарни «Колос»"


def test_lab_gets_referenced_lab_and_theme(tmp_home, tmp_path):
    # лаба 2 уже сделана; методичка лабы 7 говорит «к своей теме из ЛР №2»
    pipe, fake = setup(tmp_home, tmp_path, [{**RAW, "number": "2", "topic": "Сайт о Бобруйске"}, SEC, SEC2, ANS])
    pipe.memory = SubjectMemory()
    two = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="2")
    two = pipe.execute(pipe.confirm(pipe.parse(two, date="24.09.2026"), None))
    pipe.memory.set_theme("ivanov", "psip", "сайт о Бобруйске")
    fake.responses[:] = [RAW, SEC, SEC2, ANS]
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7"),
                    date="24.09.2026")
    assert any(f.rule_id == "spec.linked_lab" and "ЛР 2" in f.message for f in st.findings)
    calls_before = len(fake.calls)
    st = pipe.execute(pipe.confirm(st, None))
    prompts = "\n".join(m["content"] for call in fake.calls[calls_before:] for m in call)
    assert "сайт о Бобруйске" in prompts  # тематика
    assert "ЛР №2" in prompts and "@keyframes fade" in prompts  # сама прошлая лаба с кодом


def test_missing_referenced_lab_warns(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7"),
                    date="24.09.2026")
    f = next(f for f in st.findings if f.rule_id == "spec.linked_lab_missing")
    assert "ЛР 2" in f.message and "пожелани" in f.message


REPORT_TEXT = """Содержание
Лабораторная работа №1 ........ 3
Лабораторная работа №5 ........ 9
Лабораторная работа №27 ....... 40
Лабораторная работа №1
Тема: Приложение «Учёт книг» на WinForms
Главная форма FormMain с таблицей книг, класс Book.
Лабораторная работа №5
Тема: Что-то постороннее про сети, очень длинный текст. """ + "лишнее " * 400 + """
Лабораторная работа №27
Тема: Отчёты в приложении «Учёт книг»
Добавлена форма FormReport, экспорт в Excel.
"""


def test_split_labs_takes_sections_not_table_of_contents():
    from forlabs.pipeline.memory import parse_numbers, split_labs

    parts = split_labs(REPORT_TEXT)
    assert "FormMain" in parts["1"] and "FormReport" in parts["27"] and "...." not in parts["1"]
    assert parse_numbers("1, 27; ЛР 5.2  и 30") == ["1", "27", "5.2", "30"]


def _docx(path, text):
    import docx

    d = docx.Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    d.save(path)
    return path.read_bytes()


def test_chosen_labs_from_combined_report_only_those_reach_model(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [{**RAW, "assignment": "Задания по своей теме"}, SEC, SEC2, ANS])
    pipe.memory = SubjectMemory()
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="30")
    report = _docx(tmp_path / "общий.docx", REPORT_TEXT)
    st, missing = pipe.set_links(st, ["1", "27", "12"], ("общий.docx", report))
    assert st.linked == ["1", "27", "12"] and missing == ["12"]
    assert set(st.linked_texts) == {"1", "27"}  # только выбранные, ЛР 5 из отчёта не берётся
    st = pipe.parse(st, date="24.09.2026")
    msgs = [f.message for f in st.findings if f.rule_id.startswith("spec.linked")]
    assert any("ЛР 27" in m and "отчёт" in m for m in msgs) and any("ЛР 12" in m for m in msgs)
    before = len(fake.calls)
    pipe.execute(pipe.confirm(st, None))
    prompts = "\n".join(m["content"] for call in fake.calls[before:] for m in call)
    assert "FormMain" in prompts and "FormReport" in prompts and "лишнее лишнее" not in prompts
