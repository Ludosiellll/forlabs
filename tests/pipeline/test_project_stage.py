"""Этап «Проект»: из отчёта — настоящий проект, сборка с исправлениями, снимки окон, листинги из кода, папка сдачи."""

import json

from test_run import ANS, FIXTURE, RAW, setup

from forlabs.core.llm import LLMError
from forlabs.runner import languages
from forlabs.runner.shots import FakeShooter

PY_SEC = {"blocks": [
    {"type": "paragraph", "text": "Программа считает сумму двух чисел."},
    {"type": "listing", "topic": "Исходный код программы", "language": "python",
     "code": "a = float(input(\"Введите a: \"))\nb = float(input(\"Введите b: \"))\nprint(\"Сумма:\", a + b)"},
    {"type": "figure", "kind": "screenshot", "topic": "Результат работы программы",
     "instruction": "Запусти программу, введи 12 и 5"},
]}
PY_SEC2 = {"blocks": [
    {"type": "paragraph", "text": "Вид окна среды с кодом."},
    {"type": "figure", "kind": "screenshot", "topic": "Окно IDLE с кодом программы", "instruction": "Открой main.py в IDLE"},
]}
GOOD = "a = float(input(\"Введите a: \"))\nb = float(input(\"Введите b: \"))\nprint(\"Сумма:\", a + b)\n"
PLAN = {"language": "python", "kind": "console", "name": "Сумма", "entry": "main.py",
        "files": [{"path": "main.py", "content": GOOD}],
        "listings": [{"section": 1, "block": 2, "file": "main.py"}],
        "shots": [{"section": 1, "block": 3, "mode": "console", "input": "12\n5\n", "expect": "Сумма: 17"},
                  {"section": 2, "block": 2, "mode": "manual", "reason": "это окно среды разработки"}]}


def make(tmp_home, tmp_path, responses, shooter=None, tools=None, monkeypatch=None):
    pipe, fake = setup(tmp_home, tmp_path, [{**RAW, "main_tasks": ["Сумма двух чисел", "Код в IDLE"]}, PY_SEC,
                                            PY_SEC2, ANS, *responses])
    pipe.projects = True
    pipe.shooter = shooter if shooter is not None else FakeShooter()
    if monkeypatch is not None and tools is not None:
        monkeypatch.setattr(languages, "detect", lambda *a, **k: tools)
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.confirm(pipe.parse(st, date="24.09.2026"), None)
    return pipe, fake, st


def figures(st):
    return [b for s in st.report.sections for b in s.blocks if b.type == "figure"]


def test_project_built_shot_taken_and_copied_to_hand_in_folder(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    st = pipe.execute(st)
    shot, manual = figures(st)
    assert shot.image_path and (pipe.lab_dir(st) / shot.image_path).exists()  # снимок сделал ForLabs
    assert not manual.image_path and "среды разработки" in (manual.auto_note or "")  # этот — студент, с причиной
    assert st.project and st.project.built and st.project.language == "python"
    out = tmp_path / "сдача" / "ПССИП" / "ЛР7" / "Проект Сумма" / "main.py"
    assert out.read_text(encoding="utf-8") == GOOD  # проект рядом с отчётом
    call = "\n".join(m["content"] for m in fake.calls[4])
    assert "[1.2]" in call and "Введите a" in call and "[1.3]" in call  # модель видела листинги и рисунки по номерам
    target, shot_plan = pipe.shooter.calls[0]
    assert shot_plan.input == "12\n5\n" and target.console


def test_build_errors_go_back_to_model(tmp_home, tmp_path):
    broken = {**PLAN, "files": [{"path": "main.py", "content": "print(\"Сумма:\"\n"}]}
    fixed = {"files": [{"path": "main.py", "content": GOOD}], "listings": []}
    pipe, fake, st = make(tmp_home, tmp_path, [broken, fixed])
    st = pipe.execute(st)
    assert st.project.built and st.project.fixes == 1
    fix_prompt = "\n".join(m["content"] for m in fake.calls[5])
    assert "main.py" in fix_prompt and ("SyntaxError" in fix_prompt or "строк" in fix_prompt)
    assert figures(st)[0].image_path


def test_build_still_broken_after_three_rounds(tmp_home, tmp_path):
    broken = {**PLAN, "files": [{"path": "main.py", "content": "print(\"Сумма:\"\n"}]}
    still = {"files": [{"path": "main.py", "content": "print(\"Сумма:\"\n"}]}
    pipe, fake, st = make(tmp_home, tmp_path, [broken, still, still, still])
    st = pipe.execute(st)
    assert st.project and not st.project.built
    assert "не собрал" in (figures(st)[0].auto_note or "").lower() or "не собрал" in (st.project.note or "").lower()
    assert not figures(st)[0].image_path and st.status in ("rendered", "needs_manual")  # лаба не сломалась


def test_dangerous_code_is_not_run(tmp_home, tmp_path):
    bad = {**PLAN, "files": [{"path": "main.py", "content": "import os\nos.system(\"del /q C:\\\\*\")\n" + GOOD}]}
    pipe, fake, st = make(tmp_home, tmp_path, [bad])
    st = pipe.execute(st)
    assert pipe.shooter.calls == []
    assert "запуск" in (figures(st)[0].auto_note or "") and st.project.blocked
    assert (tmp_path / "сдача" / "ПССИП" / "ЛР7" / "Проект Сумма" / "main.py").exists()  # проект всё равно есть


def test_missing_tool_is_named_for_install(tmp_home, tmp_path, monkeypatch):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN], tools={}, monkeypatch=monkeypatch)
    st = pipe.execute(st)
    fig = figures(st)[0]
    assert fig.need_tool == "python" and "Python" in (fig.auto_note or "") and pipe.shooter.calls == []
    assert st.project.missing == ["python"]


def test_listing_text_follows_fixed_code(tmp_home, tmp_path):
    changed = {**PLAN, "listings": [{"section": 1, "block": 2, "file": "main.py",
                                     "code": "a = float(input(\"Введите a: \"))\nprint(\"Сумма:\", a)"}]}
    pipe, fake, st = make(tmp_home, tmp_path, [changed])
    st = pipe.execute(st)
    listing = st.report.sections[0].blocks[1]
    assert listing.code == "a = float(input(\"Введите a: \"))\nprint(\"Сумма:\", a)"


def test_project_failure_never_breaks_the_lab(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [LLMError("network", "нет связи")])
    st = pipe.execute(st)
    assert st.status in ("rendered", "needs_manual") and st.report is not None
    assert st.project is None or not st.project.built
    assert all(not f.image_path for f in figures(st))


def test_retry_uses_saved_plan_without_model(tmp_home, tmp_path, monkeypatch):
    real = languages.detect
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN], tools={}, monkeypatch=monkeypatch)
    st = pipe.execute(st)
    assert figures(st)[0].need_tool == "python"
    monkeypatch.setattr(languages, "detect", real)  # Python поставили
    calls = len(fake.calls)
    st = pipe.retry_project(st)  # «Повторить»: план уже есть, модель не нужна
    assert len(fake.calls) == calls
    assert figures(st)[0].image_path and st.project.built and not st.project.missing


def test_non_code_lab_has_no_project(tmp_home, tmp_path):
    from test_run import SEC2

    text_only = {"blocks": [{"type": "paragraph", "text": "Анимации описаны словами."}]}
    pipe, fake = setup(tmp_home, tmp_path, [RAW, text_only, SEC2, ANS])
    pipe.projects, pipe.shooter = True, FakeShooter()
    st = pipe.confirm(pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())],
                                                 number="7"), date="24.09.2026"), None)
    st = pipe.execute(st)  # в отчёте нет кода — плана нет, модель не спрашивали
    assert len(fake.calls) == 4 and st.project is None


def test_plan_saved_for_cloud_and_rebuild(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    st = pipe.execute(st)
    saved = json.loads((pipe.lab_dir(st) / "project" / "plan.json").read_text(encoding="utf-8"))
    assert saved["name"] == "Сумма" and saved["files"][0]["path"] == "main.py"
