from pathlib import Path

import pytest

from forlabs.core.llm import FakeLLM
from forlabs.core.models import Enrollment, Figure, LabSpec, Listing, Paragraph, Profile, Task
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
    from forlabs.core.models import ReportDoc, TaskSection, QA
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
