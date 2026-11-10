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
    nothing = {"labs": [{"number": "12", "found": False}]}  # ЛР 12 модель в отчёте не нашла
    pipe, fake = setup(tmp_home, tmp_path, [nothing, {**RAW, "assignment": "Задания по своей теме"}, SEC, SEC2, ANS])
    pipe.memory = SubjectMemory()
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="30")
    report = _docx(tmp_path / "общий.docx", REPORT_TEXT)
    st, missing = pipe.set_links(st, ["1", "27", "12"], ("общий.docx", report))
    assert st.linked == ["1", "27", "12"] and missing == ["12"]
    assert "Нужны работы: 12" in fake.calls[0][-1]["content"]  # модель ищет только то, что не нашлось по заголовкам
    assert set(st.linked_texts) == {"1", "27"}  # только выбранные, ЛР 5 из отчёта не берётся
    st = pipe.parse(st, date="24.09.2026")
    msgs = [f.message for f in st.findings if f.rule_id.startswith("spec.linked")]
    assert any("ЛР 27" in m and "отчёт" in m for m in msgs) and any("ЛР 12" in m for m in msgs)
    before = len(fake.calls)
    pipe.execute(pipe.confirm(st, None))
    prompts = "\n".join(m["content"] for call in fake.calls[before:] for m in call)
    assert "FormMain" in prompts and "FormReport" in prompts and "лишнее лишнее" not in prompts


def test_split_labs_accepts_other_heading_styles():
    from forlabs.pipeline.memory import split_labs

    text = ("ЛАБОРАТОРНАЯ РАБОТА 1\nФорма входа\nЛаб. работа № 2\nМеню\nЛР 3. Отчёты\nЭкспорт\n"
            "Практическая работа №4\nНастройки\nОтчёт по лабораторной работе №5.1\nПечать\n")
    parts = split_labs(text)
    assert {k: v.splitlines()[1] for k, v in parts.items()} == {
        "1": "Форма входа", "2": "Меню", "3": "Экспорт", "4": "Настройки", "5.1": "Печать"}


NO_HEADINGS = """Учёт книг
Тема: Приложение «Учёт книг» на WinForms
Главная форма FormMain с таблицей книг.
Вывод: научился делать формы.
Сети
Тема: что-то про сети
""" + "лишнее " * 400 + """
Вывод: сети работают.
Отчёты
Тема: Отчёты в приложении «Учёт книг»
Добавлена форма FormReport, экспорт в Excel.
Вывод: отчёты готовы.
"""


def test_report_without_headings_model_finds_labs_by_skeleton(tmp_home, tmp_path):
    located = {"labs": [{"number": 1, "found": True, "start": 0, "end": 3, "how": "по порядку"},
                        {"number": "27", "found": True, "start": "9", "end": 12, "how": "по смыслу"}]}
    pipe, fake = setup(tmp_home, tmp_path, [located, {**RAW, "assignment": "Задания по своей теме"}])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="30")
    st, missing = pipe.set_links(st, ["1", "27"], ("общий.docx", _docx(tmp_path / "общий.docx", NO_HEADINGS)))
    assert missing == []
    assert "FormMain" in st.linked_texts["1"] and "сети" not in st.linked_texts["1"]
    assert "FormReport" in st.linked_texts["27"] and "лишнее" not in st.linked_texts["27"]
    ask = "\n".join(m["content"] for m in fake.calls[0])
    assert "Тема: Отчёты в приложении" in ask and "лишнее лишнее" not in ask  # скелет, а не весь отчёт
    assert "Создание визуальных эффектов" in ask or "анимац" in ask  # контекст текущей лабы — из методички
    st = pipe.parse(st, date="24.09.2026")
    msg = next(f.message for f in st.findings if f.rule_id == "spec.linked_lab" and "ЛР 27" in f.message)
    assert "по смыслу" in msg and "Тема: Отчёты" in msg  # видно, что нашла модель и с чего начинается кусок


def test_model_answer_out_of_range_or_unasked_is_ignored(tmp_home, tmp_path):
    bad = {"labs": [{"number": "1", "found": True, "start": 50, "end": 60},
                    {"number": "27", "found": True, "start": 8, "end": 2},
                    {"number": "99", "found": True, "start": 0, "end": 1}]}
    pipe, _ = setup(tmp_home, tmp_path, [bad])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="30")
    st, missing = pipe.set_links(st, ["1", "27"], ("общий.docx", _docx(tmp_path / "общий.docx", NO_HEADINGS)))
    assert st.linked_texts == {} and missing == ["1", "27"]


def test_model_failure_keeps_numbers_and_reports_missing(tmp_home, tmp_path):
    from forlabs.core.llm import LLMError

    pipe, _ = setup(tmp_home, tmp_path, [LLMError("network", "нет связи")])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="30")
    st, missing = pipe.set_links(st, ["1"], ("общий.docx", _docx(tmp_path / "общий.docx", NO_HEADINGS)))
    assert st.linked == ["1"] and missing == ["1"]
    st, missing = pipe.set_links(st, ["1"], ("битый.docx", b"not a zip"))  # нечитаемый отчёт — не ошибка лабы
    assert st.linked == ["1"] and missing == ["1"]


MANUAL = """Лабораторная работа №1
Тема: Формы. Время выполнения лабораторной работы 2 часа.
Лабораторная работа №2
Тема: Меню
Лабораторная работа №3
Тема: Отчёты. Используйте результаты лабораторной работы №1. В лабораторной работе №4 будет печать.
Лабораторная работа №4
Тема: Печать
"""


def test_references_only_from_own_part_and_only_past_labs():
    assert referenced_labs(MANUAL, "3") == ["1"]  # не 2 и 4 из заголовков сборника, не будущая 4
    assert referenced_labs(MANUAL, "1") == []  # «2 часа» — не ссылка, будущие лабы — не ссылки
    assert referenced_labs("Время выполнения лабораторной работы 2 часа.", "7") == []


def test_split_labs_merges_page_headers_and_more_heading_forms():
    from forlabs.pipeline.memory import parse_numbers, split_labs

    pdf = ("Лабораторная работа №1\nстраница один\nЛабораторная работа №1\nстраница два\n"
           "Лабораторная работа №2\nстраница три\nЛабораторная работа №2\nстраница четыре\n")
    parts = split_labs(pdf)
    assert "страница один" in parts["1"] and "страница два" in parts["1"] and "страница три" not in parts["1"]
    forms = ("ОТЧЁТ О ВЫПОЛНЕНИИ ЛАБОРАТОРНОЙ РАБОТЫ №1\nа\nк лабораторной работе №2\nб\n"
             "Иванов И.И. | Лабораторная работа No 3\nв\nЛабораторная работа №04\nг\n")
    assert sorted(split_labs(forms)) == ["1", "2", "3", "4"]
    assert split_labs("Отчёт по лабораторным работам 24–28\nТема: формы\nЛабораторные работы 1, 2\n") == {}
    assert parse_numbers("01, 4") == ["1", "4"]


def test_methodology_reference_taken_from_report_without_typing(tmp_home, tmp_path):
    report = _docx(tmp_path / "общий.docx", "Лабораторная работа №2\nСайт о Бобруйске, файл index.html.\n"
                                            "Лабораторная работа №6\nГалерея gallery.html.\n")
    prev = {**RAW, "assignment": "Продолжите сайт из предыдущей лабораторной работы"}
    pipe, fake = setup(tmp_home, tmp_path, [RAW, {**prev, "number": "7"}])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7"),
                    date="24.09.2026")
    st, missing = pipe.set_links(st, [], ("общий.docx", report))  # методичка сама говорит «из ЛР №2»
    assert missing == [] and "Бобруйске" in st.linked_texts["2"] and "6" not in st.linked_texts
    assert any(f.rule_id == "spec.linked_lab" and "ЛР 2" in f.message for f in st.findings)
    assert "Бобруйске" in pipe._memory_text(st)
    # «предыдущая работа» = ЛР 6 для седьмой — тоже берётся из отчёта
    st2 = pipe.parse(pipe.create_lab("ivanov", "psip", [("м.txt", "Лабораторная работа №7\nПродолжите сайт из предыдущей лабораторной работы.".encode())]),
                     date="24.09.2026")
    st2, missing = pipe.set_links(st2, [], ("общий.docx", report))
    assert "gallery.html" in pipe._memory_text(st2)
    st3 = pipe.store.load_lab("ivanov", "psip", st2.number)
    st3.linked_texts = {}
    msgs = " ".join(f.message for f in pipe._link_findings(st3))
    assert "Нет предыдущей лабы ни в ForLabs, ни в отчёте" in msgs and "прикрепи общий отчёт" in msgs


def test_link_findings_refreshed_before_execute(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7"),
                    date="24.09.2026")
    assert any(f.rule_id == "spec.linked_lab_missing" for f in st.findings)  # ЛР 2 ещё не сделана
    fake.responses[:] = [{**RAW, "number": "2", "assignment": ""}, SEC, SEC2, ANS]
    two = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="2")
    pipe.execute(pipe.confirm(pipe.parse(two, date="24.09.2026"), None))
    fake.responses[:] = [SEC, SEC2, ANS]
    st = pipe.execute(pipe.confirm(pipe.store.load_lab("ivanov", "psip", "7"), None))
    assert not any(f.rule_id == "spec.linked_lab_missing" for f in st.findings)
    assert any(f.rule_id == "spec.linked_lab" and "ЛР 2" in f.message for f in st.findings)


def test_lab_described_in_wishes_is_not_asked_for_again(tmp_home, tmp_path):
    # студент сделал то, о чём просит замечание: описал прошлую лабу в пожеланиях — замечание уходит
    pipe, fake = setup(tmp_home, tmp_path, [RAW])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7",
                         wishes="Тема сайта из ЛР 2 — кофейня «Зерно», главная страница index.html.")
    st = pipe.parse(st, date="24.09.2026")
    assert not any(f.rule_id == "spec.linked_lab_missing" for f in st.findings)


def test_wishes_about_something_else_keep_the_note(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7",
                         wishes="Комментарии в коде пиши по-русски.")
    st = pipe.parse(st, date="24.09.2026")
    assert any(f.rule_id == "spec.linked_lab_missing" for f in st.findings)
