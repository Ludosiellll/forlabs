from pathlib import Path

import pytest

from forlabs.core.llm import FakeLLM
from forlabs.core.models import (
    Enrollment,
    Figure,
    LabSpec,
    Listing,
    Paragraph,
    Profile,
    Task,
)
from forlabs.core.rules import RuleBook
from forlabs.core.subjects import SUBJECTS
from forlabs.executors.diagrams import browser_available, render_dot
from forlabs.pipeline.repair import check_all, repair
from forlabs.pipeline.write import write_report

PROFILE = Profile(id="ivanova", full_name="Иванова Анна Петровна", title_name="А.П. Иванова", gender="f",
                  enrollments=[Enrollment(subject_id="ssp", title_subject="ССП", variant=14)])
SPEC = LabSpec(subject_id="ssp", number="8", date="29.09.2026",
               topic="Создание простейших программ работы со ссылочными типами данных Golang",
               goal="Сформировать умения создавать простейшие программы работы со ссылочными типами данных",
               equipment="Методические указания к выполнению, ОС Windows, VS Code",
               tasks=[Task(n=1, text="Выведите лесенку из n ступенек."),
                      Task(n=2, text="Изобразите схему работы программы.")],
               variant=14, questions=["Что называется функцией?"])

SECTION1 = {"blocks": [
    {"type": "paragraph", "text": "В VS Code создан файл task1.go с выводом лесенки:"},
    {"type": "dash_list", "items": ["- чтение числа n с клавиатуры;", "- вывод ступенек в цикле."]},
    {"type": "listing", "topic": "Исходный код программы вывода лесенки", "language": "go",
     "code": "package main\n\nfunc main() {\n}"},
    {"type": "figure", "kind": "screenshot", "topic": "Результат выполнения программы в консоли",
     "instruction": "Запусти go run task1.go и сделай снимок окна консоли"},
]}
SECTION2 = {"blocks": [
    {"type": "paragraph", "text": "Схема показывает порядок работы программы."},
    {"type": "figure", "kind": "diagram", "topic": "Схема работы программы", "dot": "digraph { a -> b }"},
]}
ANSWERS = {"answers": ["Именованный блок кода для многократного вызова."]}


def test_write_report_fake_llm(tmp_path):
    fake = FakeLLM([SECTION1, SECTION2, ANSWERS])
    events = []
    doc = write_report(SPEC, PROFILE, PROFILE.enrollment("ssp"), SUBJECTS["ssp"], fake, RuleBook(),
                       assets_dir=tmp_path, on_event=events.append, render_diagrams=False)
    assert [s.n for s in doc.sections] == [1, 2]
    assert doc.sections[0].title == "Выведите лесенку из n ступенек."
    fig = doc.sections[0].blocks[3]
    assert isinstance(fig, Figure) and fig.slot.startswith("Запусти") and fig.image_path is None
    assert isinstance(doc.sections[0].blocks[2], Listing)
    assert doc.questions[0].answer.startswith("Именованный")
    assert doc.conclusion == "Сформировала умения создавать простейшие программы работы со ссылочными типами данных."
    assert doc.header["Тема работы"] == SPEC.topic
    assert check_all(doc, SPEC, PROFILE, others={}) == []
    assert events and "Задание 1" in events[0]
    first_prompt = "\n".join(m["content"] for m in fake.calls[0])
    assert "женский" in first_prompt and "Выведите лесенку" in first_prompt and "rules/subjects/ssp.md" in first_prompt


def test_repair_fixes_cliche():
    from forlabs.core.models import QA, ReportDoc, TaskSection
    doc = ReportDoc(lab_number="8", header={}, sections=[TaskSection(n=1, title=SPEC.tasks[0].text, blocks=[
        Paragraph(text="Срез играет важную роль в программе."), Paragraph(text="Цикл выводит числа.")]),
        TaskSection(n=2, title=SPEC.tasks[1].text, blocks=[Paragraph(text="Схема составлена.")])],
        questions=[QA(n=1, question="Что называется функцией?", answer="Блок кода.")],
        conclusion="x")
    findings = [f for f in check_all(doc, SPEC, PROFILE, {}) if f.stage == "antiai"]
    assert [f.rule_id for f in findings] == ["ai.cliche"]
    fake = FakeLLM([{"blocks": [{"index": 1, "text": "Срез хранит ступеньки разной длины."}]}])
    fixed = repair(doc, findings, fake, RuleBook(), SPEC, PROFILE)
    assert fixed.sections[0].blocks[0].text == "Срез хранит ступеньки разной длины."
    assert fixed.sections[0].blocks[1].text == "Цикл выводит числа."
    assert len(fake.calls) == 1


def test_code_findings_in_listing():
    from forlabs.core.models import ReportDoc, TaskSection
    doc = ReportDoc(lab_number="8", sections=[TaskSection(n=1, title=SPEC.tasks[0].text, blocks=[
        Listing(topic="Код", code='fmt.Println("==========")\n// TODO', language="go")])])
    rules = {f.rule_id for f in check_all(doc, SPEC, PROFILE, {}) if f.stage == "antiai"}
    assert {"code.separator", "code.todo"} <= rules


@pytest.mark.skipif(not browser_available(), reason="нет браузера для схем")
def test_render_dot(tmp_path):
    out = render_dot("digraph { rankdir=LR; Ввод -> Обработка -> Вывод }", tmp_path / "d.png")
    from PIL import Image
    assert out is not None and Image.open(out).size[0] > 100


def test_same_opening_points_to_blocks_and_gets_repaired():
    from forlabs.core.models import ReportDoc, TaskSection
    doc = ReportDoc(lab_number="8", sections=[
        TaskSection(n=1, title=SPEC.tasks[0].text, blocks=[Paragraph(text="В программе создан массив."),
                                                            Paragraph(text="В программе описан цикл.")]),
        TaskSection(n=2, title=SPEC.tasks[1].text, blocks=[Paragraph(text="В программе выведен результат.")])],
        conclusion="x")
    fs = [f for f in check_all(doc, SPEC, PROFILE, {}) if f.rule_id == "ai.same_opening"]
    assert sorted(f.location for f in fs) == ["Задание 1, блок 2", "Задание 2, блок 1"]
    fake = FakeLLM([{"blocks": [{"index": 2, "text": "Цикл перебирает элементы."}]},
                    {"blocks": [{"index": 1, "text": "Результат выведен в консоль."}]}])
    fixed = repair(doc, fs, fake, RuleBook(), SPEC, PROFILE)
    assert not [f for f in check_all(fixed, SPEC, PROFILE, {}) if f.rule_id == "ai.same_opening"]


def test_prompt_says_no_variants_when_none():
    fake = FakeLLM([SECTION1, SECTION2, ANSWERS])
    spec = SPEC.model_copy(update={"variant_text": None})
    write_report(spec, PROFILE, PROFILE.enrollment("ssp"), SUBJECTS["ssp"], fake, RuleBook(),
                 assets_dir=Path("/tmp"), render_diagrams=False)
    prompt = "\n".join(m["content"] for m in fake.calls[0])
    assert "В этой работе нет вариантов" in prompt


def test_conclusion_override_used():
    fake = FakeLLM([SECTION1, SECTION2, ANSWERS])
    spec = SPEC.model_copy(update={"conclusion": "Сформировала навыки работы со срезами."})
    doc = write_report(spec, PROFILE, PROFILE.enrollment("ssp"), SUBJECTS["ssp"], fake, RuleBook(),
                       assets_dir=Path("/tmp"), render_diagrams=False)
    assert doc.conclusion == "Сформировала навыки работы со срезами."


def test_empty_and_ragged_tables_from_model(tmp_path):
    from forlabs.core.models import ReportDoc, Table, TaskSection
    from forlabs.pipeline.write import BlockDraft, SectionDraft, draft_to_blocks
    from forlabs.render.docx_builder import build_lab

    draft = SectionDraft(blocks=[
        BlockDraft(type="paragraph", text="Результаты замеров приведены ниже."),
        BlockDraft(type="table", topic="пустая", rows=[[]]),
        BlockDraft(type="table", topic="пустая 2", rows=[["", " "]]),
        BlockDraft(type="table", topic="результаты замеров", rows=[["Опыт", "Время, мс"], ["1"], ["2", "14", "лишнее"]]),
    ])
    blocks = draft_to_blocks(draft, tmp_path, 1, render_diagrams=False)
    tables = [b for b in blocks if isinstance(b, Table)]
    assert len(tables) == 1
    assert tables[0].rows == [["Опыт", "Время, мс", ""], ["1", "", ""], ["2", "14", "лишнее"]]
    doc = ReportDoc(lab_number="1", header={"Тема": "Т"}, conclusion="Изучил.",
                    sections=[TaskSection(n=1, title="Сделать замеры.", blocks=blocks)])
    assert build_lab(doc, tmp_path / "t.docx", author="x").exists()


def test_colored_diagram_becomes_white_not_black():
    from forlabs.executors.diagrams import styled

    out = styled('digraph { bgcolor="lightblue"; a [style=filled, color="lightblue"]; '
                 'b [style=filled, fillcolor="#ffcc00"]; a -> b [color=red]; }')
    assert 'bgcolor="lightblue"' not in out and 'bgcolor="black"' not in out
    assert 'fillcolor="white"' in out.split("];", 1)[1]  # заливка узлов по умолчанию — белая
    assert '"#ffcc00"' not in out and "red" not in out


@pytest.mark.skipif(not browser_available(), reason="нет браузера")
def test_colored_diagram_renders_light(tmp_path):
    from PIL import Image

    out = render_dot('digraph { bgcolor="lightblue"; node [style=filled, color="navy"]; Ввод -> Обработка -> Вывод }',
                     tmp_path / "c.png")
    with Image.open(out) as im:
        px = im.convert("L").tobytes()
    dark = sum(1 for v in px if v < 100) / len(px)
    assert dark < 0.08, f"слишком много тёмного: {dark:.0%}"


def test_report_level_findings_do_not_spin_repair_rounds():
    from forlabs.core.models import ReportDoc, TaskSection
    from forlabs.pipeline.repair import check_and_repair, report_text

    doc = ReportDoc(lab_number="8", header={}, conclusion="Изучила.", sections=[
        TaskSection(n=1, title=SPEC.tasks[0].text, blocks=[Paragraph(text="Массив заполнен числами с клавиатуры.")]),
        TaskSection(n=2, title=SPEC.tasks[1].text, blocks=[Paragraph(text="Сумма выводится после цикла.")])])
    events = []
    fake = FakeLLM([])  # модель не должна вызываться: такое замечание правится только «Переписать»
    doc2, findings = check_and_repair(doc, SPEC, PROFILE, fake, RuleBook(), {"Петров": report_text(doc)},
                                      on_event=events.append)
    assert any(f.rule_id == "ai.similar_profile" for f in findings)
    assert fake.calls == [] and not any("исправляю" in e for e in events)


def test_markdown_rule_ignores_python_dunder_names():
    from forlabs.core.models import ReportDoc, TaskSection
    from forlabs.report.standard import check_standard

    doc = ReportDoc(lab_number="8", header={}, conclusion="x", sections=[TaskSection(
        n=1, title=SPEC.tasks[0].text, blocks=[Paragraph(text="Конструктор __init__ заполняет поля name и age.")])])
    assert not [f for f in check_standard(doc, SPEC, "f") if f.rule_id == "text.markdown"]
    doc.sections[0].blocks[0] = Paragraph(text="Это **важно** для работы.")
    assert [f for f in check_standard(doc, SPEC, "f") if f.rule_id == "text.markdown"]


# ---------- своя фамилия вместо «Фамилия» из методички ----------

def _asked_for(spec, tmp_path, profile=PROFILE) -> str:
    fake = FakeLLM([SECTION1, SECTION2, {"answers": ["Именованный блок кода."]}])
    write_report(spec, profile, profile.enrollment("ssp"), SUBJECTS["ssp"], fake, RuleBook(), tmp_path,
                 render_diagrams=False)
    return "\n".join(m["content"] for m in fake.calls[0])


def test_students_surname_is_given_when_the_methodology_asks_to_use_it(tmp_path):
    spec = SPEC.model_copy(update={"tasks": [Task(n=1, text="Создать базу данных World_Фамилия."), SPEC.tasks[1]]})
    asked = _asked_for(spec, tmp_path)
    assert "Фамилия студента: Иванова" in asked and "Ivanova" in asked and "не придумывай" in asked


def test_placeholder_in_the_methodology_text_is_enough(tmp_path):
    spec = SPEC.model_copy(update={"source_text": "Назовите проект своей фамилией и сохраните его."})
    assert "Фамилия студента: Иванова" in _asked_for(spec, tmp_path)


def test_surname_is_not_sent_when_nobody_asks_for_it(tmp_path):
    asked = _asked_for(SPEC, tmp_path)
    assert "Иванова" not in asked and "Фамилия студента" not in asked


# ---------- картинки методички в запросе на текст ----------

def test_writer_is_told_what_picture_blocks_are(tmp_path):
    spec = SPEC.model_copy(update={"source_text": "Выполните запросы с рисунков.\n[Картинка в методичке: SELECT 1;]\n"
                                                  "[Картинка в методичке — прочитать не удалось]"})
    asked = _asked_for(spec, tmp_path)
    assert "содержание картинок методички" in asked and "не выдумывай" in asked


def test_no_picture_note_when_the_methodology_has_no_pictures(tmp_path):
    spec = SPEC.model_copy(update={"source_text": "Выполните запросы."})
    assert "содержание картинок методички" not in _asked_for(spec, tmp_path)


# ---------- чего нет в методичке ----------

def test_needs_reach_the_writer(tmp_path):
    spec = SPEC.model_copy(update={"needs": ["тестовые сценарии из практической работы №6"]})
    asked = _asked_for(spec, tmp_path)
    assert "практической работы №6" in asked and "не утверждай" in asked
    assert "Для работы нужно" not in _asked_for(SPEC, tmp_path)


NOTE = ("Пояснительная записка к проекту политики информационной безопасности SPA-салона.\n"
        "1. Структура организации и границы системы. SPA-салон оказывает услуги массажа, ухода за кожей и "
        "косметологии. Штат: директор, администратор, два мастера, бухгалтер.\n"
        "2. Защищаемые ресурсы. К ним относятся база записей клиентов, данные об оплате и рабочие места.\n"
        "- резервное копирование базы записей раз в неделю;\n"
        "- антивирусная защита на всех компьютерах.\n"
        "3. Ответственность. За соблюдение политики отвечает администратор салона.")


def test_document_written_as_a_listing_becomes_ordinary_text(tmp_path):
    from forlabs.core.models import DashList, Listing, Paragraph
    from forlabs.pipeline.write import BlockDraft, SectionDraft, draft_to_blocks

    draft = SectionDraft(blocks=[BlockDraft(type="listing", topic="Пояснительная записка к проекту политики",
                                            language="text", code=NOTE)])
    blocks = draft_to_blocks(draft, tmp_path, 1, render_diagrams=False)
    assert not any(isinstance(b, Listing) for b in blocks)
    paras = [b.text for b in blocks if isinstance(b, Paragraph)]
    assert paras[0].startswith("Пояснительная записка") and any(p.startswith("2. Защищаемые") for p in paras)
    lists = [b for b in blocks if isinstance(b, DashList)]
    assert len(lists) == 1 and len(lists[0].items) == 2


def test_code_and_file_contents_stay_listings(tmp_path):
    from forlabs.core.models import Listing
    from forlabs.pipeline.write import BlockDraft, SectionDraft, draft_to_blocks

    code = "static void Main()\n{\n    Console.WriteLine(\"Привет, мир! Это первая программа.\");\n}"
    data = ("Иванов Иван Петрович, группа РС02-24, средний балл 8.\n"
            "Петрова Анна Сергеевна, группа РС02-24, средний балл 9.")
    draft = SectionDraft(blocks=[
        BlockDraft(type="listing", topic="Исходный код программы", language="csharp", code=code),
        BlockDraft(type="listing", topic="Содержимое файла students.txt", language="text", code=data),
        BlockDraft(type="listing", topic="Вывод программы", language="text",
                   code="Введите ключ: ключ принят.\nШифротекст: 85E813540F0AB405")])
    blocks = draft_to_blocks(draft, tmp_path, 1, render_diagrams=False)
    assert [type(b) for b in blocks] == [Listing, Listing, Listing]


def test_writer_is_told_documents_are_text_not_listings():
    from forlabs.pipeline.prompts import CHAT_REVISE_SYSTEM, WRITE_SYSTEM

    for prompt in (WRITE_SYSTEM, CHAT_REVISE_SYSTEM):
        assert "пояснительн" in prompt.lower() and "listing" in prompt


def _doc(blocks):
    from forlabs.core.models import ReportDoc, TaskSection

    return ReportDoc(lab_number="5", header={}, sections=[TaskSection(n=1, title="Зашифровать текст", blocks=blocks)],
                     conclusion="Научилась шифровать.")


def test_program_lab_without_any_code_is_flagged():
    spec = SPEC.model_copy(update={"assignment": "Создать программу по шифрованию текста, используя криптосистему DES.",
                                   "tasks": [Task(n=1, text="Зашифровать исходный текст (не менее 8 символов).")]})
    words = _doc([Paragraph(text="Создала консольное приложение, которое шифрует текст алгоритмом DES.")])
    found = [f for f in check_all(words, spec, PROFILE, {}) if f.rule_id == "report.no_code"]
    assert len(found) == 1 and found[0].severity == "error" and found[0].location == "Задание 1"
    with_code = _doc([Paragraph(text="Код программы представлен в листинге 1."),
                      Listing(topic="Исходный код программы", code="class P { static void Main() {} }",
                              language="csharp")])
    assert not [f for f in check_all(with_code, spec, PROFILE, {}) if f.rule_id == "report.no_code"]


def test_lab_without_a_program_task_needs_no_code():
    spec = SPEC.model_copy(update={"assignment": "Изучить программное обеспечение компьютера и составить отчёт.",
                                   "tasks": [Task(n=1, text="Создать базу данных и модель с помощью Reverse Engineer.")]})
    doc = _doc([Paragraph(text="Изучила установленное программное обеспечение.")])
    assert not [f for f in check_all(doc, spec, PROFILE, {}) if f.rule_id == "report.no_code"]
