"""Честные сообщения этапа «Проект»: несобранная программа не названа собранной; код игрового движка и других
сред, которые ForLabs не собирает, не превращается в чужой проект."""

from test_project_stage import PLAN, figures, make
from test_run import ANS, FIXTURE, RAW, setup

from forlabs.pipeline import project as proj
from forlabs.runner.plan import ProjectPlan, validate
from forlabs.runner.shots import FakeShooter


def journal(pipe, st):
    return [e.text for e in pipe.journal(st)]


def test_failed_build_is_not_called_built(tmp_home, tmp_path):
    broken = {**PLAN, "files": [{"path": "main.py", "content": "print(\"Сумма:\"\n"}]}
    still = {"files": [{"path": "main.py", "content": "print(\"Сумма:\"\n"}]}
    pipe, fake, st = make(tmp_home, tmp_path, [broken, still, still, still])
    st = pipe.execute(st)
    assert not st.project.built
    said = journal(pipe, st)
    assert not any(t.startswith("Программа собрана") for t in said)
    assert any("не собралась" in t for t in said)


def test_built_program_is_still_called_built(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    st = pipe.execute(st)
    assert any(t.startswith("Программа собрана") for t in journal(pipe, st))


UNITY = {"blocks": [
    {"type": "paragraph", "text": "Скрипт GameMenu.cs управляет кнопками меню."},
    {"type": "listing", "topic": "Исходный код скрипта GameMenu.cs", "language": "csharp",
     "code": "using UnityEngine;\nusing UnityEngine.UI;\n\npublic class GameMenu : MonoBehaviour\n{\n"
             "    public Button playButton;\n\n    void Start()\n    {\n"
             "        playButton.onClick.AddListener(() => Debug.Log(\"Играть\"));\n    }\n}"},
    {"type": "figure", "kind": "screenshot", "topic": "Сцена Menu в режиме Play",
     "instruction": "Unity: запусти режим Play и сними окно Game"},
]}


def unity_lab(tmp_home, tmp_path):
    text_only = {"blocks": [{"type": "paragraph", "text": "Элементы настроены в инспекторе."}]}
    pipe, fake = setup(tmp_home, tmp_path, [RAW, UNITY, text_only, ANS])
    pipe.projects, pipe.shooter = True, FakeShooter()
    st = pipe.confirm(pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())],
                                                 number="7"), date="24.09.2026"), None)
    return pipe, fake, pipe.execute(st)


def test_unity_script_is_not_turned_into_a_project(tmp_home, tmp_path):
    pipe, fake, st = unity_lab(tmp_home, tmp_path)
    assert len(fake.calls) == 4 and st.project is None  # план проекта у модели не просили
    assert proj.engine_of(st) == "Unity" and not proj.needs_project(st)


def test_unity_figures_say_why_the_student_takes_them(tmp_home, tmp_path):
    pipe, fake, st = unity_lab(tmp_home, tmp_path)
    note = figures(st)[0].auto_note or ""
    assert "Unity" in note and "сам" in note
    assert any("Unity" in t for t in journal(pipe, st))


def test_plain_csharp_is_still_a_project():
    from forlabs.core.models import Listing

    assert proj.engine_in([Listing(topic="Код", language="csharp",
                                   code="using System;\nclass P { static void Main() { } }")]) is None
    assert proj.engine_in([Listing(topic="Код", language="java",
                                   code="import android.os.Bundle;\npublic class MainActivity extends "
                                        "AppCompatActivity { }")]) == "Android"
    assert proj.engine_in([Listing(topic="Код", language="cpp",
                                   code="void setup() {\n  pinMode(13, OUTPUT);\n}\nvoid loop() {\n"
                                        "  digitalWrite(13, HIGH);\n}")]) == "Arduino"


def test_window_shot_of_a_program_that_first_reads_the_console_is_rejected():
    # ЛР 6 по Java: Scanner в консоли, окно — только после ввода; снимок окна не дождался бы его
    code = ("import java.util.Scanner;\nimport javax.swing.JFrame;\n\npublic class CircleTest extends JFrame {\n"
            "    public static void main(String[] args) {\n        int r = new Scanner(System.in).nextInt();\n"
            "        new CircleTest().setVisible(true);\n    }\n}\n")
    plan = ProjectPlan.model_validate({
        "language": "java", "kind": "gui", "name": "Circle", "entry": "CircleTest.java",
        "files": [{"path": "CircleTest.java", "content": code}],
        "listings": [{"section": 1, "block": 1, "file": "CircleTest.java"}],
        "shots": [{"section": 1, "block": 2, "mode": "window", "expect": ""}]})
    problems = validate(plan)
    assert any("1.2" in p and "консол" in p for p in problems)
    ok = plan.model_copy(update={"files": [plan.files[0].model_copy(update={
        "content": code.replace("new Scanner(System.in).nextInt()", "100").replace("import java.util.Scanner;\n", "")})]})
    assert validate(ok) == []


def test_figure_of_a_program_forlabs_cannot_drive_is_not_given_to_another_one():
    # рисунок «Окно среды разработки Eclipse», а модель выбрала ближайшую из списка — IntelliJ IDEA
    from forlabs.core.models import Figure
    from forlabs.runner.plan import ShotPlan

    fig = Figure(topic="Окно среды разработки Eclipse с открытым проектом", slot="Eclipse: открой проект")
    sh = ShotPlan(section=1, block=4, mode="app", app="intellij", goal="открыть проект")
    assert proj.foreign_app(sh, fig) == "Eclipse"
    own = Figure(topic="Окно IntelliJ IDEA с кодом", slot="IntelliJ IDEA: открой Main.java")
    assert proj.foreign_app(sh, own) is None
    plain = Figure(topic="Окно среды разработки с кодом программы", slot="Открой Main.java в редакторе")
    assert proj.foreign_app(sh, plain) is None
    # Visual Studio Code и Visual Studio — разные программы, но рисунок про VS Code не «чужой» для vscode
    code = Figure(topic="Окно Visual Studio Code", slot="")
    assert proj.foreign_app(ShotPlan(section=1, block=1, mode="app", app="vscode", goal="x"), code) is None
