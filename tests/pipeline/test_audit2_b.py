"""Аудит-2, модель и методичка: JSON, лимиты, Tesseract, кодировки, управляющие символы, номера, код."""

import json

import httpx
import pytest
from test_run import ANS, FIXTURE, RAW, SEC, SEC2, setup

from forlabs.core.llm import LLM, LLMError, _extract_json
from forlabs.core.settings import Settings


def http_llm(handler, **kw):
    return LLM(Settings(), api_key="k", http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def ok(content: str):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})


# B1: ``` внутри строки JSON не ломает разбор
def test_json_with_fence_inside_string():
    text = json.dumps({"blocks": [{"type": "listing", "code": "```python\nprint(1)\n```"}]})
    assert _extract_json(text)["blocks"][0]["code"].startswith("```python")
    assert _extract_json("```json\n{\"a\": 1}\n```") == {"a": 1}


# B2: null и числа там, где ждём строку, — без платных починок
def test_lenient_types_in_drafts():
    from forlabs.pipeline.write import SectionDraft
    from forlabs.spec.extract import RawSpec

    raw = RawSpec.model_validate({"topic": None, "goal": None, "assignment": None,
                                  "variants": [{"text": None, "basis": None, "appendix": None}]})
    assert raw.goal == "" and raw.variants[0].basis == "journal"
    d = SectionDraft.model_validate({"blocks": [{"type": "table", "rows": [[1, 2.5], ["a", None]]}]})
    assert d.blocks[0].rows == [["1", "2.5"], ["a", ""]]


# B3: у каждого вызова есть предел ответа; обрыв прокси по времени не повторяется трижды
def test_calls_have_max_tokens(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    assert fake.max_tokens and all(m for m in fake.max_tokens)


def test_proxy_timeout_is_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(504, json={"error": {"message": "provider timeout"}})

    llm = http_llm(handler, retry_delays=(0, 0))
    with pytest.raises(LLMError) as e:
        llm.chat([{"role": "user", "content": "?"}])
    assert len(calls) == 1 and "не ответила" in e.value.message_ru


def test_gateway_kill_546_is_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(546, text="WORKER_LIMIT")

    with pytest.raises(LLMError):
        http_llm(handler, retry_delays=(0, 0)).chat([{"role": "user", "content": "?"}])
    assert len(calls) == 1


# B4: путь Tesseract из настроек доходит до распознавания
def test_tesseract_setting_is_used(tmp_home, tmp_path, monkeypatch):
    from forlabs.pipeline import run

    seen = {}

    def fake_extract(files, tesseract=None, pictures=False):
        seen["t"] = tesseract
        from forlabs.ingest.extract import SourceText
        return SourceText("")

    monkeypatch.setattr(run, "extract_many", fake_extract)
    pipe, fake = setup(tmp_home, tmp_path, [])
    pipe.tesseract = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    pipe.parse(pipe.create_lab("ivanov", "psip", [("фото.png", b"x")]))
    assert seen["t"] == pipe.tesseract


# B5: методичка .txt в кодировке Windows (cp1251) и UTF-16
@pytest.mark.parametrize("encoding", ["cp1251", "utf-16"])
def test_txt_in_windows_encodings(tmp_path, encoding):
    from forlabs.ingest.extract import extract_text

    f = tmp_path / "методичка.txt"
    f.write_bytes("Лабораторная работа №3. Цель работы: изучить циклы.".encode(encoding))
    res = extract_text(f)
    assert "Лабораторная работа №3" in res.text and "\ufffd" not in res.text


# B6: управляющие символы из PDF или модели не ломают вёрстку навсегда
def test_control_characters_are_removed(tmp_path):
    from forlabs.core.models import Listing, Paragraph, ReportDoc, TaskSection
    from forlabs.ingest.extract import normalize
    from forlabs.render.docx_builder import build_lab

    assert normalize("за\x02дание 1") == "задание 1"
    doc = ReportDoc(lab_number="1", header={"Тема": "Ци\x1bклы"}, conclusion="Изучил циклы.",
                    sections=[TaskSection(n=1, title="Задание\x02 один", blocks=[
                        Paragraph(text="Текст\x00 с мусором\ufffe."), Listing(topic="код", code="a = 1\x1b[0m")])])
    build_lab(doc, tmp_path / "r.docx", author="Иванов\x01 Иван")
    assert (tmp_path / "r.docx").stat().st_size > 1000


# B7: сборник методичек — в запрос попадает нужная лаба, а не первые 200 тысяч символов
def test_booklet_is_cut_around_the_lab(monkeypatch):
    from forlabs.core.llm import FakeLLM
    from forlabs.core.models import Enrollment
    from forlabs.core.subjects import get_subject
    from forlabs.ingest.extract import SourceText
    from forlabs.spec import extract

    monkeypatch.setattr(extract, "MAX_SOURCE_CHARS", 2000)
    text = "".join(f"Лабораторная работа №{i}\nТема {i}. " + "текст " * 120 + "\n" for i in range(1, 15))
    fake = FakeLLM([{**RAW, "number": "14"}])
    spec, findings = extract.extract_spec(SourceText(text), get_subject("psip"), Enrollment(subject_id="psip", title_subject="ПССИП"),
                                          fake, "14", "24.09.2026")
    sent = fake.calls[0][-1]["content"]
    assert "Лабораторная работа №14" in sent
    assert any(f.rule_id == "spec.truncated" for f in findings)


# B9: план правки с ключом type вместо kind
def test_chat_plan_type_alias():
    from forlabs.pipeline.chat import EditPlan

    assert EditPlan.model_validate({"type": "edit", "tasks": [{"n": 1, "instruction": "x"}]}).kind == "edit"


# A8: номер лабы «№ 7», «ЛР 7.», «7/8»
@pytest.mark.parametrize("raw,clean", [("№ 7", "7"), ("ЛР 7.", "7"), ("Лабораторная работа №3", "3"),
                                       ("7/8", "7-8"), ("5.2", "5.2"), ("  12 ", "12")])
def test_lab_number_is_normalized(raw, clean):
    from forlabs.pipeline.run import normalize_number

    assert normalize_number(raw) == clean


def test_slash_number_lab_is_reachable(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [])
    st = pipe.create_lab("ivanov", "psip", [("m.txt", b"x")], number="3/1")
    assert st.number == "3-1"


# A9: занятый номер — в документе номер из методички, а не «7-2»
def test_collision_keeps_document_number(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, RAW])
    first = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())]),
                       date="24.09.2026")
    second = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())]),
                        date="24.09.2026")
    assert first.number == "7" and second.number == "7-2"
    assert second.spec.number == "7"
    assert any("7-2" in e.text for e in pipe.bus.history(pipe.key(second)))


# A7: облако не приняло запись под новым номером — черновик остаётся рабочим
def test_renumber_save_failure_rolls_back(tmp_home, tmp_path):
    from forlabs.cloud.auth import CloudError

    pipe, fake = setup(tmp_home, tmp_path, [RAW])
    draft = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())])
    real = pipe.store.save_lab

    def save(st):
        if st.number == "7":
            raise CloudError("Нет связи с облаком. Проверь интернет.", offline=True)
        return real(st)

    pipe.store.save_lab = save
    st = pipe.parse(draft, date="24.09.2026")
    assert st.number == draft.number and st.status == "failed"
    assert (pipe.lab_dir(st) / "source").exists()


# A12: две новые схемы в одной правке — разные файлы
def test_diagram_files_are_unique(tmp_path, monkeypatch):
    from forlabs.pipeline import write

    made = []
    monkeypatch.setattr(write, "render_dot", lambda dot, out: (made.append(out), out.write_bytes(b"png"), out)[2])
    d = write.SectionDraft.model_validate({"blocks": [
        {"type": "figure", "kind": "diagram", "dot": "digraph{a->b}", "topic": "Схема"}]})
    write.draft_to_blocks(d, tmp_path, 1, True, name_suffix="_r1")
    write.draft_to_blocks(d, tmp_path, 1, True, name_suffix="_r1")
    assert len(set(made)) == 2


# A13: журнал с битой строкой и понятные ошибки
def test_journal_survives_broken_bytes(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [])
    st = pipe.create_lab("ivanov", "psip", [("m.txt", b"x")])
    (pipe.lab_dir(st) / "journal.log").write_bytes("info\tхорошо\n".encode() + b"info\t\xd0\n")
    assert pipe.journal(st)


def test_unexpected_english_error_is_explained(tmp_home, tmp_path):
    import zipfile

    pipe, fake = setup(tmp_home, tmp_path, [])
    st = pipe.create_lab("ivanov", "psip", [("m.txt", b"x")])
    st = pipe._fail(st, zipfile.BadZipFile("File is not a zip file"))
    assert "повреждён" in st.error
    st = pipe._fail(st, ValueError("something broke"))
    assert st.error.startswith("Непредвиденная ошибка")


# A14: замена файлов с записью на диск (fsync) — после выключения не пустой файл
def test_durable_replace_syncs(tmp_path, monkeypatch):
    import os

    from forlabs.core.fs import durable_replace

    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd)))
    tmp, target = tmp_path / "a.tmp", tmp_path / "a.docx"
    tmp.write_bytes(b"new")
    durable_replace(tmp, target)
    assert target.read_bytes() == b"new" and synced


# E2: код, который не скомпилируется
def test_bsl_backslash_quotes_and_client_server_calls():
    from forlabs.antiai.code import scan_code

    code = ('&НаКлиенте\nПроцедура Создать(Команда)\n    Док = Документы.Приход.СоздатьДокумент();\n'
            '    Эл = Справочники.Склады.НайтиПоНаименованию("ООО \\"Вектор\\"");\nКонецПроцедуры')
    ids = {f.rule_id for f in scan_code(code, "bsl", "Задание 1, блок 1")}
    assert {"code.bsl_backslash_quote", "code.bsl_server_on_client"} <= ids


def test_java_public_class_matches_file_name():
    from forlabs.antiai.code import java_class_mismatch

    assert java_class_mismatch("public class OrganizationTest {}", "Запусти файл Organization.java")
    assert not java_class_mismatch("public class Organization {}", "Запусти файл Organization.java")


# E3: прежние задания передаются с кодом, а не одной пометкой
def test_previous_code_is_passed_forward(tmp_home, tmp_path):
    from forlabs.core.models import Listing, TaskSection
    from forlabs.core.subjects import get_subject
    from forlabs.pipeline.write import _context

    pipe, fake = setup(tmp_home, tmp_path, [RAW])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())]), date="24.09.2026")
    prev = [TaskSection(n=1, title="t", blocks=[Listing(topic="style.css", code=".cafe { color: teal; }")])]
    ctx = _context(st.spec, pipe._profile(st), get_subject("psip"), "", prev)
    assert ".cafe { color: teal; }" in ctx["previous_block"]


# E4–E6: правила
def test_rules_have_no_shared_coffee_example_and_ftp_note():
    from forlabs.core.rules import RuleBook

    rb = RuleBook()
    assert "кофейн" not in rb.load("write", "psip").lower()
    assert "FileZilla" in rb.load("write", "ks")


def test_split_words_are_verbatim():
    from forlabs.core.models import LabSpec, Task
    from forlabs.spec.extract import verify_verbatim

    spec = LabSpec(subject_id="1c", number="5", date="1.1.2026", topic="t", goal="g",
                   tasks=[Task(n=1, text="Создать обработку Личные данные", kind="main")])

    class Src:
        text = "Задание. Создать обработку Лич ные данные и форму."

    assert [f for f in verify_verbatim(spec, Src()) if f.location.startswith("Задание")] == []


def test_code_line_length_rule_in_prompt():
    from forlabs.pipeline.prompts import WRITE_SYSTEM

    assert "80 символов" in WRITE_SYSTEM


# E11: вопросы картинками — предупреждение, а не тишина
def test_questions_heading_without_questions_warns(tmp_home, tmp_path):
    raw = {**RAW, "questions": []}
    pipe, fake = setup(tmp_home, tmp_path, [raw])
    st = pipe.create_lab("ivanov", "psip", [("m.txt", "Задание 1. Сделать.\nКонтрольные вопросы\n".encode())])
    st = pipe.parse(st, date="24.09.2026")
    assert any(f.rule_id == "spec.questions_missing" for f in st.findings)



# A15: правка в чате чинит только изменённое и не трогает принятые замечания
def test_chat_repair_scope():
    from forlabs.core.llm import FakeLLM
    from forlabs.core.models import (
        QA,
        LabSpec,
        Paragraph,
        Profile,
        ReportDoc,
        TaskSection,
    )
    from forlabs.core.rules import RuleBook
    from forlabs.pipeline.repair import check_and_repair

    bad = "Таким образом, данный подход является ключевым аспектом."  # штампы — замечания anti-ai
    doc = ReportDoc(lab_number="1", sections=[TaskSection(n=1, title="a", blocks=[Paragraph(text=bad)]),
                                              TaskSection(n=2, title="b", blocks=[Paragraph(text=bad)])],
                    questions=[QA(n=1, question="q", answer="Ответ.")])
    spec = LabSpec(subject_id="psip", number="1", date="1.1.2026", topic="t", goal="g")
    fake = FakeLLM([])
    check_and_repair(doc, spec, Profile(id="p", full_name="И И", title_name="И"), fake, RuleBook(), {},
                     scope=lambda f: False)
    assert fake.calls == []  # ничего вне правки не чинится


# A11: «Запомнить» не удалось — правка остаётся
def test_remember_failure_keeps_edit(tmp_home, tmp_path):
    from forlabs.pipeline.memory import SubjectMemory

    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))

    class BrokenMemory(SubjectMemory):
        def add_teacher(self, *a):
            raise RuntimeError("нет связи")

    pipe.memory = BrokenMemory()
    plan = {"kind": "edit", "reply": "Поправил.", "remember": "Комментарии к каждому методу",
            "tasks": [{"n": 2, "instruction": "короче"}], "questions": []}
    fake.responses += [plan, SEC2]
    st = pipe.chat(st, "Преподаватель: короче", remember=True)
    last = st.chat[-1]
    assert last.kind == "edit" and "Не получилось запомнить" in last.text
