"""«Своя программа»: отчёт пишется по программе студента, проект собирается из его файлов дословно."""

from test_project_stage import GOOD, PLAN, figures, make

from forlabs.pipeline import project as proj
from forlabs.runner.plan import ProjectFile, ProjectPlan


def said(call) -> str:
    return "\n".join(m["content"] for m in call)


def journal(pipe, st) -> str:
    return "\n".join(e.text for e in pipe.journal(st))


def _own(tmp_path, files):
    src = tmp_path / "моя_программа"
    for rel, data in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return src


def hand_in(tmp_path):
    return tmp_path / "сдача" / "ПССИП" / "ЛР7" / "Проект Сумма"


def test_report_and_project_follow_the_students_program(tmp_home, tmp_path):
    # модель «забыла» текст и дописала свой файл — в проект идут ровно файлы студента
    model_plan = {**PLAN, "files": [{"path": "main.py", "content": ""},
                                    {"path": "helper.py", "content": "print('чужое')\n"}]}
    pipe, fake, st = make(tmp_home, tmp_path, [model_plan])
    st, problems = pipe.set_own(st, _own(tmp_path, {"main.py": GOOD, "logo.png": b"PNG"}), "моя_программа")
    assert problems == [] and st.own.language == "python" and st.own.files == ["main.py"]
    calls = len(fake.calls)
    st = pipe.execute(st)
    writes = [said(c) for c in fake.calls[calls:calls + 2]]
    assert all("Программа студента" in w and "Введите a" in w for w in writes)  # каждое задание видит его код
    assert "дословно" in writes[0]
    plan_call = said(fake.calls[calls + 3])
    assert "Программа студента" in plan_call and "content" in plan_call
    assert st.project.built and figures(st)[0].image_path
    out = hand_in(tmp_path)
    assert (out / "main.py").read_text(encoding="utf-8") == GOOD and not (out / "helper.py").exists()
    assert (out / "logo.png").read_bytes() == b"PNG"  # ресурсы программы — рядом с кодом
    assert "поправил" not in journal(pipe, st)


def test_files_the_build_needed_to_change_are_named(tmp_home, tmp_path):
    broken = "print(\"Сумма:\"\n"
    fixed = {"files": [{"path": "main.py", "content": GOOD}], "listings": []}
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, fixed])
    st, _ = pipe.set_own(st, _own(tmp_path, {"main.py": broken}), "моя_программа")
    st = pipe.execute(st)
    assert st.project.built and st.project.fixes == 1
    assert "поправил" in journal(pipe, st) and "main.py" in journal(pipe, st)


def test_unity_program_goes_to_listings_only(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [])
    src = _own(tmp_path, {"Assets/Scripts/Player.cs": "using UnityEngine;\npublic class Player : MonoBehaviour { }",
                          "Assets/Scripts/Player.cs.meta": "guid: 1", "ProjectSettings/ProjectVersion.txt": "x"})
    st, _ = pipe.set_own(st, src, "Game.zip")
    assert st.own.engine == "Unity"
    calls = len(fake.calls)
    st = pipe.execute(st)
    assert len(fake.calls) == calls + 3  # два задания и ответы — плана проекта нет
    assert all("Unity" in (f.auto_note or "") for f in figures(st) if not f.image_path)


def test_engine_is_found_in_code_without_project_folders(tmp_home, tmp_path):
    pipe, _, st = make(tmp_home, tmp_path, [])
    st, _ = pipe.set_own(st, _own(tmp_path, {"Player.cs": "using UnityEngine;\nclass Player : MonoBehaviour { }"}),
                         "скрипты")
    assert st.own.engine == "Unity"


def test_removing_the_program(tmp_home, tmp_path):
    pipe, _, st = make(tmp_home, tmp_path, [])
    st, _ = pipe.set_own(st, _own(tmp_path, {"main.py": GOOD}), "моя_программа")
    assert "Введите a" in pipe._memory_text(st)
    st = pipe.remove_own(st)
    assert st.own is None and not (pipe.lab_dir(st) / "own").exists()
    assert "Программа студента" not in pipe._memory_text(st)


def test_bad_upload_is_a_problem_not_a_crash(tmp_home, tmp_path):
    pipe, _, st = make(tmp_home, tmp_path, [])
    st, problems = pipe.set_own(st, _own(tmp_path, {"a.png": b"PNG"}), "картинки")
    assert st.own is None and problems and "нет кода" in problems[0]


def test_student_files_replace_what_the_model_wrote():
    plan = ProjectPlan(language="csharp", kind="winforms", name="Calc", entry="Program.cs",
                       files=[ProjectFile(path="Program.cs", content="// модель"),
                              ProjectFile(path="Extra.cs", content="class Extra {}")],
                       listings=[])
    own = {"Program.cs": "static class Program { static void Main() { } }",
           "Form1.cs": "partial class Form1 { }",
           "Properties/AssemblyInfo.cs": "[assembly: AssemblyTitle(\"Calc\")]",
           "Properties/Settings.Designer.cs": "class Settings : ApplicationSettingsBase { }"}
    out = proj.with_own(plan, own)
    assert {f.path: f.content for f in out.files} == {"Program.cs": own["Program.cs"], "Form1.cs": own["Form1.cs"]}


def test_settings_kept_when_the_program_uses_them():
    plan = ProjectPlan(language="csharp", kind="winforms", name="Calc", entry="Program.cs", files=[], listings=[])
    own = {"Program.cs": "class P { static void Main() { var x = Properties.Settings.Default.Name; } }",
           "Properties/Settings.Designer.cs": "class Settings { }"}
    assert "Properties/Settings.Designer.cs" in {f.path for f in proj.with_own(plan, own).files}


def test_earlier_fixes_survive_a_new_plan():
    previous = ProjectPlan(language="python", kind="console", name="Сумма", entry="main.py",
                           files=[ProjectFile(path="main.py", content=GOOD)], listings=[])
    plan = previous.model_copy(update={"files": [ProjectFile(path="main.py", content="")]})
    out = proj.with_own(plan, {"main.py": "print(\"Сумма:\"\n"}, previous=previous)
    assert out.files[0].content == GOOD


def test_plan_memory_keeps_teacher_requirements_with_a_big_program(tmp_home, tmp_path):
    pipe, _, st = make(tmp_home, tmp_path, [])
    st, _ = pipe.set_own(st, _own(tmp_path, {"main.py": GOOD + "# " + "x" * 9000 + "\n"}), "моя_программа")
    assert "Программа студента" in pipe._memory_text(st)
    assert "Программа студента" not in pipe._memory_text(st, own=False)  # план получает её отдельно, целиком
