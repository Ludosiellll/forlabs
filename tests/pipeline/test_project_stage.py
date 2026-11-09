"""Этап «Проект»: из отчёта — настоящий проект, сборка с исправлениями, снимки окон, листинги из кода, папка сдачи."""

import json
from pathlib import Path

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


def test_project_name_with_colon_does_not_lose_the_stage(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [{**PLAN, "name": "Сумма: «два» числа"}])
    st = pipe.execute(st)
    assert st.project and st.project.built and st.project.name == "Сумма два числа"
    assert (Path(st.project.folder) / "main.py").exists() and ":" not in st.project.folder.split("/")[-1]


def test_big_project_is_planned_in_parts_when_answer_is_cut(tmp_home, tmp_path):
    cut = LLMError("bad_response", "finish_reason=length", "Ответ модели не поместился в лимит длины")
    head = {**PLAN, "files": [{"path": "calc.py"}, {"path": "main.py"}]}
    calc = {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}
    main = {"path": "main.py", "content": GOOD}
    pipe, fake, st = make(tmp_home, tmp_path, [cut, head, calc, main])
    st = pipe.execute(st)
    assert st.project and st.project.built and figures(st)[0].image_path
    work = tmp_path / "сдача" / "ПССИП" / "ЛР7" / "Проект Сумма"
    assert (work / "main.py").read_text(encoding="utf-8") == GOOD and (work / "calc.py").exists()
    ask_head, ask_calc, ask_main = ("\n".join(m["content"] for m in c) for c in fake.calls[5:8])
    assert "без текста файлов" in ask_head and "[1.2]" in ask_head  # план — с листингами и снимками
    assert "Напиши файл calc.py" in ask_calc and "Напиши файл main.py" in ask_main
    assert "def add" in ask_main and "[1.2]" in ask_main  # уже написанное и свои листинги — в запросе


def test_part_plan_is_not_tried_on_network_errors(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [LLMError("network", "нет связи")])
    st = pipe.execute(st)
    assert len(fake.calls) == 5 and (st.project is None or not st.project.built)


def test_teacher_data_file_beginning_goes_to_the_model(tmp_home, tmp_path):
    from forlabs.core.models import Material
    from forlabs.pipeline.project import lab_text

    pipe, fake, st = make(tmp_home, tmp_path, [])
    rows = "\n".join(f"01.10.2026;Товар {i};{i},50" for i in range(40))
    st.materials = [Material(name="sales.csv", text="Дата;Товар;Цена\n" + rows), Material(name="task.py", text="x")]
    text = lab_text(st)
    assert "Начало файла sales.csv" in text and "Дата;Товар;Цена" in text and "всего строк: 41" in text
    assert "Товар 30" not in text and "Начало файла task.py" not in text  # только начало и только данные


def test_php_site_pages_open_from_site_root(tmp_home, tmp_path, monkeypatch):
    from forlabs.runner.languages import BuildResult, Toolchain

    tools = {"php": Toolchain("php", "php"), "edge": Toolchain("edge", "msedge")}
    monkeypatch.setattr(languages, "build", lambda d, plan, tools, timeout=300: BuildResult(True, "", d / plan.entry))
    site = {"language": "php", "kind": "web", "name": "Заказ", "entry": "order.php",
            "files": [{"path": "index.html", "content": "<form action=\"order.php\" method=\"post\"></form>"},
                      {"path": "order.php", "content": "<?php echo 'Сумма: ' . (2 * 3); ?>"}],
            "shots": [{"section": 1, "block": 3, "mode": "browser", "url": "index.html"},
                      {"section": 2, "block": 2, "mode": "manual", "reason": "нужно отправить форму"}]}
    pipe, fake, st = make(tmp_home, tmp_path, [site], tools=tools, monkeypatch=monkeypatch)
    st = pipe.execute(st)
    target, shot = pipe.shooter.calls[0]
    assert target.server and target.url.endswith("/order.php")
    assert shot.url.startswith("http://127.0.0.1:") and shot.url.endswith("/index.html")
    assert "order.php" not in shot.url  # не «…/order.php/index.html»: та же главная и без стилей
    assert figures(st)[0].image_path


def test_fix_request_names_listings_of_the_files(tmp_home, tmp_path):
    broken = {**PLAN, "files": [{"path": "main.py", "content": "print(\"Сумма:\"\n"}]}
    pipe, fake, st = make(tmp_home, tmp_path, [broken, {"files": [{"path": "main.py", "content": GOOD}]}])
    pipe.execute(st)
    assert "[1.2] из main.py" in "\n".join(m["content"] for m in fake.calls[5])


def test_listing_follows_code_that_next_task_extended(tmp_home, tmp_path):
    grown = GOOD.replace("print(", "print(\"Числа:\", a, b)\nprint(", 1)  # строка внутри кода листинга
    pipe, fake, st = make(tmp_home, tmp_path, [{**PLAN, "files": [{"path": "main.py", "content": grown}]}])
    st = pipe.execute(st)
    assert st.report.sections[0].blocks[1].code == grown.rstrip("\n")  # модель листинг не вернула — взят из файла


def test_listing_in_file_keeps_fragment_bounds():
    from forlabs.pipeline.project import listing_in_file

    body = ("def a():\n    return 1\n\n\ndef total(xs):\n    s = 0\n    for x in xs:\n        s += x\n"
            "    return s\n\n\nprint(total([1, 2]))\n")
    frag = "def total(xs):\n    s = 0\n    for x in xs:\n        s = s + x\n    return s"
    assert listing_in_file(frag, body) == "def total(xs):\n    s = 0\n    for x in xs:\n        s += x\n    return s"
    assert listing_in_file("x = input()\ny = x * 2\nprint(y)", body) is None  # чужого кода в файле нет


def test_build_files_from_model_are_dropped_not_fatal(tmp_home, tmp_path):
    extra = [{"path": "Сумма.csproj", "content": "<Project/>"}, {"path": "run.bat", "content": "python main.py"}]
    pipe, fake, st = make(tmp_home, tmp_path, [{**PLAN, "files": [*PLAN["files"], *extra]}])
    st = pipe.execute(st)
    assert st.project and st.project.built and figures(st)[0].image_path
    work = Path(st.project.folder)
    assert (work / "main.py").exists() and not (work / "run.bat").exists() and not (work / "Сумма.csproj").exists()


def test_build_files_in_fix_answer_are_ignored(tmp_home, tmp_path):
    broken = {**PLAN, "files": [{"path": "main.py", "content": "print(\"Сумма:\"\n"}]}
    fixed = {"files": [{"path": "go.mod", "content": "module x\n"}, {"path": "main.py", "content": GOOD}]}
    pipe, fake, st = make(tmp_home, tmp_path, [broken, fixed])
    st = pipe.execute(st)
    assert st.project.built and st.project.fixes == 1 and not (Path(st.project.folder) / "go.mod").exists()


def test_whole_file_listing_gets_imports_added_for_build(tmp_home, tmp_path):
    listing = "import math\n\n" + GOOD.rstrip("\n")
    body = "import sys\nimport math\n\n" + GOOD
    sec = {"blocks": [{**b, "code": listing} if b["type"] == "listing" else b for b in PY_SEC["blocks"]]}
    pipe, fake = setup(tmp_home, tmp_path, [{**RAW, "main_tasks": ["Сумма двух чисел", "Код в IDLE"]}, sec, PY_SEC2,
                                            ANS, {**PLAN, "files": [{"path": "main.py", "content": body}]}])
    pipe.projects, pipe.shooter = True, FakeShooter()
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    assert st.report.sections[0].blocks[1].code == body.rstrip("\n")  # импорт для сборки — и в листинге


CPP = "#include <iostream>\nint main() { int a, b; std::cin >> a >> b; std::cout << \"Сумма: \" << a + b; }\n"


def test_console_title_is_the_program_in_hand_in_folder(tmp_home, tmp_path, monkeypatch):
    from forlabs.runner.languages import BuildResult, Toolchain

    exe = lambda d, plan, tools, timeout=300: BuildResult(True, "", Path(d) / "out" / "Summa.exe")  # noqa: E731
    monkeypatch.setattr(languages, "build", exe)
    plan = {**PLAN, "language": "cpp", "entry": "main.cpp", "files": [{"path": "main.cpp", "content": CPP}],
            "listings": []}
    pipe, fake, st = make(tmp_home, tmp_path, [plan], tools={"gpp": Toolchain("gpp", "g++")}, monkeypatch=monkeypatch)
    st = pipe.execute(st)
    target, _ = pipe.shooter.calls[0]
    assert target.title == str(Path(st.project.folder) / "out" / "Summa.exe")  # как у студента, а не папка запуска
    assert "ForLabs" not in Path(target.title).parts and str(pipe.lab_dir(st)) not in target.title


def test_interpreter_keeps_its_own_console_title(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    pipe.execute(st)
    target, _ = pipe.shooter.calls[0]
    assert target.title is None and target.cmd[-1] == "main.py"  # заголовок — путь python, как при обычном запуске


def test_static_page_opens_from_hand_in_folder(tmp_home, tmp_path, monkeypatch):
    from forlabs.runner.languages import Toolchain

    site = {"language": "web", "kind": "web", "name": "Сайт", "entry": "index.html",
            "files": [{"path": "index.html", "content": "<h1>Конвертер</h1><a href=\"about.html\">О нас</a>"},
                      {"path": "about.html", "content": "<h1>О нас</h1>"}],
            "shots": [{"section": 1, "block": 3, "mode": "browser", "url": "about.html#contacts"},
                      {"section": 2, "block": 2, "mode": "browser"}]}
    pipe, fake, st = make(tmp_home, tmp_path, [site], tools={"edge": Toolchain("edge", "msedge")},
                          monkeypatch=monkeypatch)
    st = pipe.execute(st)
    folder = Path(st.project.folder)
    (t1, s1), (t2, s2) = pipe.shooter.calls
    assert t1.url == (folder / "index.html").as_uri() and t1.cwd == folder
    assert s1.url == (folder / "about.html").as_uri() + "#contacts" and not s2.url  # без адреса — главная из t2.url
    for url in (t1.url, s1.url):
        assert "ForLabs" not in url and pipe.lab_dir(st).as_uri() not in url


def test_expect_keeps_only_the_label_before_a_number_the_model_computed(tmp_home, tmp_path):
    shots = [{**PLAN["shots"][0], "expect": "Сумма: 18.0"}, PLAN["shots"][1]]  # модель ошиблась в счёте
    pipe, fake, st = make(tmp_home, tmp_path, [{**PLAN, "shots": shots}])
    st = pipe.execute(st)
    assert pipe.shooter.calls[0][1].expect == "Сумма:" and figures(st)[0].image_path


def test_expect_numbers_from_report_or_input_stay():
    from forlabs.core.models import Figure, LabSpec, LabState, ReportDoc, TaskSection
    from forlabs.pipeline.project import _trusted_expects
    from forlabs.runner.plan import ProjectPlan

    st = LabState(profile_id="p", subject_id="psip", number="1",
                  spec=LabSpec(subject_id="psip", number="1", date="", topic="Т", goal="Ц"),
                  report=ReportDoc(lab_number="1", sections=[TaskSection(n=1, title="З", blocks=[
                      Figure(topic="Результат", slot="введи 6 и 4; в кадре «S = 24,0»")])]))
    plan = ProjectPlan.model_validate({**PLAN, "shots": [
        {"section": 1, "block": 1, "mode": "console", "input": "6\n4\n", "expect": "S = 24.0"},
        {"section": 1, "block": 1, "mode": "console", "input": "7\n", "expect": "Введено: 7"},
        {"section": 1, "block": 1, "mode": "console", "input": "7\n", "expect": "Итого — 52 руб."}]})
    assert [s.expect for s in _trusted_expects(plan, st).shots] == ["S = 24.0", "Введено: 7", "Итого"]


def test_no_typing_into_a_program_that_reads_nothing(tmp_home, tmp_path):
    quiet = "print(\"Сумма:\", 12 + 5)\n"
    shots = [{**PLAN["shots"][0], "input": "\n", "expect": "Сумма:"}, PLAN["shots"][1]]
    pipe, fake, st = make(tmp_home, tmp_path, [{**PLAN, "files": [{"path": "main.py", "content": quiet}],
                                                "shots": shots}])
    pipe.execute(st)
    assert pipe.shooter.calls[0][1].input == ""  # лишний Enter закрыл бы «pause» и окно консоли
    # программа с input() ввод сохраняет — см. test_project_built_shot_taken_and_copied_to_hand_in_folder


def test_dangerous_code_is_not_even_built(tmp_home, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(languages, "build", lambda *a, **k: calls.append(1) or languages.BuildResult(True, "", None))
    bad = {**PLAN, "files": [{"path": "main.py", "content": "import os\nos.system(\"calc\")\n" + GOOD}]}
    pipe, fake, st = make(tmp_home, tmp_path, [bad])
    st = pipe.execute(st)
    assert calls == [] and st.project.blocked and not st.project.built  # сборки не было: опасное — до неё
