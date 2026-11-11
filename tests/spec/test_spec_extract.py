import os
from pathlib import Path

import pytest

from forlabs.core.llm import LLM, FakeLLM
from forlabs.core.models import Enrollment
from forlabs.core.settings import Settings
from forlabs.core.subjects import SUBJECTS
from forlabs.ingest.extract import SourceText, normalize
from forlabs.spec.extract import extract_spec, merge_subitems, one_line, verify_verbatim

SRC = SourceText(normalize((Path(__file__).parents[1] / "fixtures" / "methodology_psip_lr7.txt").read_text("utf-8")))
ENR = Enrollment(subject_id="psip", title_subject="ПССИП", variant=14, header_style="short")
TASKS = ["Создайте 2 простых анимации к выбранной тематике.", "Создайте анимацию с эффектом параллакса.",
         "Создайте анимированное меню.", "Создайте анимированные кнопки.", "Создайте слайдер изображений.",
         "Создайте анимированный заголовок."]
RAW = {"number": "7", "topic": "Создание визуальных эффектов и анимации на HTML странице.",
       "goal": "Сформировать умение создавать визуальные эффекты и анимации на HTML – странице.",
       "equipment": "Методические указания к выполнению, ОС Windows, Sublime Text, MS Word.",
       "assignment": "Задания выполняются к своей теме из ЛР №2", "main_tasks": TASKS[:5], "extra_tasks": TASKS[5:],
       "variant_text": None,
       "questions": ["Hover-эффекты и их применение.", "Назовите наиболее распространенные hover-эффекты.",
                     "Алгоритм действия эффекта перекатывания.", "Применение hover-эффектов к кнопкам."]}


def test_extract_with_fake_llm():
    spec, findings = extract_spec(SRC, SUBJECTS["psip"], ENR, FakeLLM([RAW]), lab_number=None, date="24.09.2026")
    assert findings == []
    assert [t.n for t in spec.tasks] == [1, 2, 3, 4, 5, 6]
    assert spec.tasks[5].kind == "extra" and spec.tasks[0].kind == "main"
    assert spec.number == "7" and spec.variant == 14 and spec.date == "24.09.2026"
    assert spec.topic == "Создание визуальных эффектов и анимации на HTML странице"
    assert spec.goal.endswith("HTML – странице")
    assert len(spec.questions) == 4


def test_numbers_stripped_and_lines_joined():
    raw = dict(RAW, main_tasks=["1. Создайте 2 простых анимации\nк выбранной тематике."] + TASKS[1:5])
    spec, findings = extract_spec(SRC, SUBJECTS["psip"], ENR, FakeLLM([raw]), lab_number="7", date="24.09.2026")
    assert spec.tasks[0].text == TASKS[0]
    assert findings == []


def test_verbatim_detects_paraphrase():
    raw = dict(RAW, main_tasks=["Сделать две анимации по теме."] + TASKS[1:5])
    spec, findings = extract_spec(SRC, SUBJECTS["psip"], ENR, FakeLLM([raw]), lab_number="7", date="24.09.2026")
    assert [f.rule_id for f in findings] == ["spec.not_verbatim"]
    assert findings[0].location == "Задание 1"


def test_verify_question_paraphrase():
    raw = dict(RAW, questions=["Что такое hover?"] + RAW["questions"][1:])
    spec, _ = extract_spec(SRC, SUBJECTS["psip"], ENR, FakeLLM([raw]), lab_number="7", date="24.09.2026")
    assert [f.location for f in verify_verbatim(spec, SRC)] == ["Вопрос 1"]


def test_prompt_mentions_variant_and_verbatim():
    fake = FakeLLM([RAW])
    extract_spec(SRC, SUBJECTS["psip"], ENR, fake, lab_number=None, date="24.09.2026")
    prompt = "\n".join(m["content"] for m in fake.calls[0])
    assert "дословно" in prompt.lower() and "14" in prompt
    assert "Создайте анимированное меню" in prompt


@pytest.mark.live
def test_extract_live():
    s = Settings(base_url=os.environ.get("FORLABS_BASE_URL", Settings().base_url),
                 model=os.environ.get("FORLABS_MODEL", Settings().model))
    llm = LLM(s, api_key=os.environ["FORLABS_API_KEY"])
    spec, findings = extract_spec(SRC, SUBJECTS["psip"], ENR, llm, lab_number=None, date="24.09.2026")
    assert spec.number == "7"
    assert "визуальных эффектов" in spec.topic
    assert len(spec.tasks) == 6
    assert len(spec.questions) == 4
    assert [f for f in findings if f.severity == "error"] == []


SSP_ENR = Enrollment(subject_id="ssp", title_subject="ССП", variant=14, workplace=2)
SSP_SRC = SourceText(normalize(
    "Лабораторная работа №4\nТема работы: Ссылочные типы Java.\nЦель работы: Сформировать умения.\n"
    "Задание №1:\nВыполните задание согласно варианту , указанному в приложении А.\n"
    "Вариант выбрать в соответствии с номером рабочего места.\nЗадание №2.\n"
    "Построить иерархию классов в соответствии с вариантом задания, указанным в приложении Б. Вариант выбрать в "
    "соответствии с номером по журналу.\nДля разработанной Вами иерархии классов в методе Main:\n"
    "1) описать массив объектов базового класса;\n2) занести в этот массив объекты дочерних классов;\n"
    "3) продемонстрировать работу методов класса у всех элементов этого массива.\n"
    "Приложение А\n2. Создать класс Payment (покупка) с внутренним классом.\nПриложение Б\n14 Организация\n"))
SSP_RAW = {"number": "4", "topic": "Ссылочные типы Java", "goal": "Сформировать умения",
           "assignment": "Выполните задание согласно варианту , указанному в приложении А.",
           "main_tasks": ["Выполните задание согласно варианту , указанному в приложении А. Вариант выбрать в "
                          "соответствии с номером рабочего места.",
                          "Построить иерархию классов в соответствии с вариантом задания, указанным в приложении Б. "
                          "Вариант выбрать в соответствии с номером по журналу. Для разработанной Вами иерархии "
                          "классов в методе Main:",
                          "описать массив объектов базового класса;",
                          "занести в этот массив объекты дочерних классов;",
                          "продемонстрировать работу методов класса у всех элементов этого массива."],
           "extra_tasks": [],
           "variants": [{"appendix": "Приложение А", "basis": "workplace", "number": 2,
                         "text": "Создать класс Payment (покупка) с внутренним классом."},
                        {"appendix": "Приложение Б", "basis": "journal", "number": 14, "text": "Организация"}],
           "questions": []}


def test_subitems_merged_into_parent_task():
    spec, findings = extract_spec(SSP_SRC, SUBJECTS["ssp"], SSP_ENR, FakeLLM([SSP_RAW]), lab_number="4",
                                  date="16.09.2026")
    assert len(spec.tasks) == 2
    assert spec.tasks[1].text.endswith("в методе Main: 1) описать массив объектов базового класса; "
                                       "2) занести в этот массив объекты дочерних классов; 3) продемонстрировать "
                                       "работу методов класса у всех элементов этого массива.")
    assert findings == []


def test_lowercase_item_without_colon_parent_stays_separate():
    assert merge_subitems(["Создать форму входа.", "добавить кнопку выхода."]) == \
        ["Создать форму входа.", "добавить кнопку выхода."]


def test_subitems_keep_letter_markers():
    assert merge_subitems(["Сделать в методе Main:", "а) описать массив;", "б) заполнить его."]) == \
        ["Сделать в методе Main: а) описать массив; б) заполнить его."]


def test_one_line_keeps_compound_hyphen():
    assert one_line("Создать веб-\nстраницу") == "Создать веб-страницу"


def test_space_before_comma_and_assignment_not_a_task():
    spec, _ = extract_spec(SSP_SRC, SUBJECTS["ssp"], SSP_ENR, FakeLLM([SSP_RAW]), lab_number="4", date="16.09.2026")
    assert spec.tasks[0].text.startswith("Выполните задание согласно варианту, указанному")
    assert spec.assignment == ""


def test_variants_by_workplace_and_journal():
    fake = FakeLLM([SSP_RAW])
    spec, _ = extract_spec(SSP_SRC, SUBJECTS["ssp"], SSP_ENR, fake, lab_number="4", date="16.09.2026")
    assert "Приложение А (по номеру рабочего места, вариант 2): Создать класс Payment" in spec.variant_text
    assert "Приложение Б (по журналу, вариант 14): Организация" in spec.variant_text
    prompt = "\n".join(m["content"] for m in fake.calls[0])
    assert "рабочего места" in prompt and "2" in prompt


def test_workplace_missing_warns():
    # рабочее место не указано — вариант по месту берётся по журналу (14), и об этом сказано студенту
    enr = Enrollment(subject_id="ssp", title_subject="ССП", variant=14)
    src = SourceText(SSP_SRC.text.replace("2. Создать класс Payment", "14. Создать класс Payment"))
    raw = {**SSP_RAW, "variants": [{**SSP_RAW["variants"][0], "number": 14}, SSP_RAW["variants"][1]]}
    spec, findings = extract_spec(src, SUBJECTS["ssp"], enr, FakeLLM([raw]), lab_number="4", date="16.09.2026")
    assert [f.rule_id for f in findings] == ["spec.workplace_missing"]


# ---------- задания под заголовками «Задание N» с нумерованными шагами ----------

from forlabs.spec.extract import clean_item, task_headings  # noqa: E402

DB = SourceText(normalize("""Лабораторная работа №7
Тема работы: Добавление данных в таблицы
Цель работы: Сформировать умения использовать операцию добавления данных в таблицы.
Задание: Изучить основные теоретические сведения, выполнить задания 1-2, ответить на контрольные вопросы.
Порядок выполнения работы
Задание 1
1.Заполнить таблицы БД Forum информацией.
2. Представить скриншоты скриптов заполнения таблиц.
3. Представить скриншоты заполненных таблиц.
Задание 2
1. Заполнить таблицы БД Store данными.
2. Привести скриншоты заполнения таблиц.
Контрольные вопросы и задания:
Опишите синтаксис команды INSERT?
"""))
ENR_DB = Enrollment(subject_id="ssp", title_subject="СУБД", variant=7, header_style="long")
T1 = ("1.Заполнить таблицы БД Forum информацией. 2. Представить скриншоты скриптов заполнения таблиц. "
      "3. Представить скриншоты заполненных таблиц.")
T2 = "1. Заполнить таблицы БД Store данными. 2. Привести скриншоты заполнения таблиц."
RAW_DB = {"number": "7", "topic": "Добавление данных в таблицы",
          "goal": "Сформировать умения использовать операцию добавления данных в таблицы.", "equipment": None,
          "assignment": "Изучить основные теоретические сведения, выполнить задания 1-2, ответить на контрольные вопросы.",
          "main_tasks": [T1, T2], "extra_tasks": [], "variant_text": None,
          "questions": ["Опишите синтаксис команды INSERT?"]}


def parse(*raws):
    fake = FakeLLM(list(raws))
    spec, findings = extract_spec(DB, SUBJECTS["ssp"], ENR_DB, fake, lab_number=None, date="10.11.2026")
    return spec, findings, fake


def test_task_headings_are_counted():
    assert task_headings(DB.text) == 2
    assert task_headings("Задание: Изучите методические указания.\n1. Сделать одно.\n2. Сделать другое.") == 0
    assert task_headings("Задание №1. Написать программу\nЗадание №2. Нарисовать схему\nЗадание №3: Вывод") == 3
    assert task_headings("Задание 1. Одно\nЗадание 3. Не по порядку") == 0  # не подряд — это не список заданий


def test_steps_of_a_task_keep_all_their_numbers():
    assert clean_item(T1).startswith("1.Заполнить") and " 2. Представить" in clean_item(T1)
    assert clean_item("1. Создайте 2 простых анимации к выбранной тематике.").startswith("Создайте 2 простых")
    spec, findings, _ = parse(RAW_DB)
    assert [t.text for t in spec.tasks] == [T1, T2] and findings == []


def test_dropped_step_numbers_are_not_a_different_wording():
    bare = ("Заполнить таблицы БД Forum информацией. Представить скриншоты скриптов заполнения таблиц. "
            "Представить скриншоты заполненных таблиц.")
    spec, findings, _ = parse(dict(RAW_DB, main_tasks=[bare, T2]))
    assert [f.rule_id for f in findings] == []  # текст тот же, выпали только номера шагов
    reworded = bare.replace("Заполнить таблицы", "Наполнить все таблицы")
    assert [f.rule_id for f in parse(dict(RAW_DB, main_tasks=[reworded, T2]))[1]] == ["spec.not_verbatim"]


def test_model_is_told_how_many_tasks_the_methodology_has():
    _, _, fake = parse(RAW_DB)
    asked = fake.calls[0][-1]["content"]
    assert "«Задание 1»" in asked and "ровно 2" in asked and "шаги" in asked


def test_steps_split_into_separate_tasks_are_asked_again():
    split = dict(RAW_DB, main_tasks=["Заполнить таблицы БД Forum информацией.",
                                     "Представить скриншоты скриптов заполнения таблиц.",
                                     "Представить скриншоты заполненных таблиц.", "Заполнить таблицы БД Store данными.",
                                     "Привести скриншоты заполнения таблиц."])
    spec, findings, fake = parse(split, RAW_DB)
    assert len(fake.calls) == 2 and [t.text for t in spec.tasks] == [T1, T2]
    assert "5" in fake.calls[1][-1]["content"] and "ровно 2" in fake.calls[1][-1]["content"]


def test_still_wrong_number_of_tasks_is_told_to_the_student():
    split = dict(RAW_DB, main_tasks=["Заполнить таблицы БД Forum информацией.", "Заполнить таблицы БД Store данными.",
                                     "Привести скриншоты заполнения таблиц."])
    spec, findings, fake = parse(split, split)
    assert len(spec.tasks) == 3 and len(fake.calls) == 2
    note = next(f for f in findings if f.rule_id == "spec.tasks_count")
    assert note.severity == "warn" and "2" in note.message and "3" in note.message


# ---------- картинки методички и раздел «Контрольные вопросы» ----------

def test_prompt_explains_picture_blocks():
    fake = FakeLLM([RAW])
    extract_spec(SRC, SUBJECTS["psip"], ENR, fake, lab_number=None, date="24.09.2026")
    system = fake.calls[0][0]["content"]
    assert "[Картинка в методичке" in system and "прочитать не удалось" in system


def test_questions_only_mentioned_in_the_assignment_are_not_missing():
    # «…ответить на контрольные вопросы» в строке «Задание» — не раздел вопросов
    src = SourceText(SRC.text.replace("Контрольные вопросы", "Вопросы для защиты")
                     + "\nЗадание: выполнить задания и ответить на контрольные вопросы.")
    raw = {**RAW, "questions": []}
    _, findings = extract_spec(src, SUBJECTS["psip"], ENR, FakeLLM([raw]), lab_number=None, date="24.09.2026")
    assert "spec.questions_missing" not in [f.rule_id for f in findings]


def test_questions_section_without_questions_is_still_reported():
    assert "Контрольные вопросы" in SRC.text
    raw = {**RAW, "questions": []}
    _, findings = extract_spec(SRC, SUBJECTS["psip"], ENR, FakeLLM([raw]), lab_number=None, date="24.09.2026")
    assert "spec.questions_missing" in [f.rule_id for f in findings]


def test_wording_that_continues_into_a_picture_is_verbatim():
    # вопрос «…и почему:» + код, который в методичке стоит картинкой сразу после него
    src = SourceText(SRC.text + "\n5. Что будет выведено на консоль и почему:\n[Картинка в методичке:\n"
                     "public class Program {\n    int a = 5;\n}\n]\n")
    q = "Что будет выведено на консоль и почему: public class Program { int a = 5; }"
    raw = {**RAW, "questions": [*RAW["questions"], q]}
    _, findings = extract_spec(src, SUBJECTS["psip"], ENR, FakeLLM([raw]), lab_number=None, date="24.09.2026")
    assert findings == []


VAR_SRC = SourceText(normalize("""Лабораторная работа №20
Тема: Разработка программ записи данных в JSON-файл.
Цель: Сформировать умение записывать данные в JSON-файл.
Индивидуальные задания:
Вариант 1: Создать класс «Студент» с полями: Имя, Фамилия. Записать массив студентов в JSON-файл.
Вариант 2: Создать класс «Книга» с полями: Название, Автор. Записать список книг в JSON.
"""))
VAR_TASK = "Создать класс «Книга» с полями: Название, Автор. Записать список книг в JSON."
RAW_VAR = {"number": "20", "topic": "Разработка программ записи данных в JSON-файл.",
           "goal": "Сформировать умение записывать данные в JSON-файл.", "equipment": None, "assignment": None,
           "main_tasks": [VAR_TASK], "extra_tasks": [], "variant_text": None, "questions": []}


def test_no_tasks_at_all_is_asked_again_with_reasoning():
    fake = FakeLLM([dict(RAW_VAR, main_tasks=[]), RAW_VAR])
    enr = Enrollment(subject_id="kpiyap", title_subject="КПиЯП", variant=2, header_style="short")
    spec, _ = extract_spec(VAR_SRC, SUBJECTS["kpiyap"], enr, fake, lab_number=None, date="11.11.2026")
    assert [t.text for t in spec.tasks] == [VAR_TASK]
    assert len(fake.calls) == 2 and fake.thinks == [False, True]
    assert "нет ни одного задания" in fake.calls[1][-1]["content"]
