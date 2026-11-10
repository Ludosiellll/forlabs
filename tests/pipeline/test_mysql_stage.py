"""Лаба по SQL: скрипты проверяются на своём сервере до отчёта (ошибка — модель чинит, как ошибку сборки), каждый
снимок начинается с чистого сервера, сообщения говорят про скрипты, а не про «программу»."""

from test_project_stage import MYSQL_PLAN, figures, make

from forlabs.pipeline import project
from forlabs.runner.languages import Toolchain

TOOLS = {"mysql": Toolchain("mysql", "/opt/mysql/bin/mysql")}


class Server:
    """Сервер-заглушка: помнит, что с ним делали; скрипт с «SELEC» считает ошибочным."""

    def __init__(self, log):
        self.log = log

    def client(self):
        return ["/opt/mysql/bin/mysql", "--no-defaults", "-u", "root", "-h", "127.0.0.1", "--port=40404"]

    def reset(self):
        self.log.append("reset")

    def run_file(self, path, timeout=120):
        text = path.read_text(encoding="utf-8")
        self.log.append(("run", path.name))
        return f"{path.name}: ERROR 1064 (42000) at line 2: You have an error in your SQL syntax" if "SELEC " in text \
            else ""

    def stop(self):
        self.log.append("stop")


def run(tmp_home, tmp_path, monkeypatch, responses):
    log = []
    monkeypatch.setattr(project, "start_mysql", lambda client: log.append("start") or Server(log))
    pipe, fake, st = make(tmp_home, tmp_path, responses, tools=TOOLS, monkeypatch=monkeypatch)
    return pipe, fake, pipe.execute(st), log


def test_scripts_are_checked_before_shots_on_one_server(tmp_home, tmp_path, monkeypatch):
    pipe, fake, st, log = run(tmp_home, tmp_path, monkeypatch, [MYSQL_PLAN])
    assert log[0] == "start" and log[-1] == "stop" and log.count("start") == 1
    assert ("run", "shop.sql") in log and log.index(("run", "shop.sql")) < len(log) - 1
    assert st.project.built and all(f.image_path for f in figures(st))


def test_every_shot_starts_on_a_clean_server(tmp_home, tmp_path, monkeypatch):
    pipe, fake, st, log = run(tmp_home, tmp_path, monkeypatch, [MYSQL_PLAN])
    after_check = log[len(log) - 1 - log[::-1].index(("run", "shop.sql")):]
    assert after_check.count("reset") >= 3  # после проверки и перед каждым из двух снимков


def test_broken_script_goes_to_the_model_like_a_build_error(tmp_home, tmp_path, monkeypatch):
    broken = {**MYSQL_PLAN, "files": [{"path": "shop.sql", "content": "CREATE DATABASE shop;\nSELEC 1;\n"}]}
    fixed = {"files": [{"path": "shop.sql", "content": "CREATE DATABASE shop;\nSELECT 1;\n"}], "listings": []}
    pipe, fake, st, log = run(tmp_home, tmp_path, monkeypatch, [broken, fixed])
    assert st.project.built and st.project.fixes == 1
    asked = "\n".join(m["content"] for m in fake.calls[5])
    assert "shop.sql" in asked and "1064" in asked and "line 2" in asked
    assert all(f.image_path for f in figures(st))


def test_script_that_stays_broken_is_shown_to_the_student_and_not_shot(tmp_home, tmp_path, monkeypatch):
    broken = {**MYSQL_PLAN, "files": [{"path": "shop.sql", "content": "CREATE DATABASE shop;\nSELEC 1;\n"}]}
    still = {"files": [{"path": "shop.sql", "content": "CREATE DATABASE shop;\nSELEC 2;\n"}], "listings": []}
    pipe, fake, st, log = run(tmp_home, tmp_path, monkeypatch, [broken, still, still, still])
    assert not st.project.built and "1064" in st.project.build_log
    assert pipe.shooter.calls == [] and not any(f.image_path for f in figures(st))
    said = [e.text for e in pipe.journal(st)]
    assert any("Скрипты SQL" in t and "ошибк" in t for t in said)
    assert not any("Программа" in t for t in said)  # про SQL говорим «скрипты», а не «программа собрана»
    assert log[-1] == "stop"


def test_good_scripts_are_reported_as_checked_not_as_a_built_program(tmp_home, tmp_path, monkeypatch):
    pipe, fake, st, log = run(tmp_home, tmp_path, monkeypatch, [MYSQL_PLAN])
    said = [e.text for e in pipe.journal(st)]
    assert any(t.startswith("Скрипты SQL проверены") for t in said)
    assert not any(t.startswith("Программа собрана") for t in said)


def test_script_order_follows_the_shots_then_the_plan():
    from forlabs.runner.plan import ProjectPlan

    plan = ProjectPlan.model_validate({
        "language": "mysql", "kind": "sql", "name": "Форум", "entry": "select.sql",
        "files": [{"path": "select.sql", "content": "SELECT 1;"}, {"path": "create.sql", "content": "SELECT 2;"},
                  {"path": "fill.sql", "content": "SELECT 3;"}, {"path": "notes.txt", "content": "x"}],
        "shots": [{"section": 1, "block": 1, "mode": "console", "input": "source create.sql\nSOURCE fill.sql;\n"},
                  {"section": 1, "block": 2, "mode": "console", "input": "\\. create.sql\nsource select.sql\n"}]})
    assert project.sql_order(plan) == ["create.sql", "fill.sql", "select.sql"]
    no_source = plan.model_copy(update={"shots": []})
    assert project.sql_order(no_source) == ["select.sql", "create.sql", "fill.sql"]


def test_rules_for_the_model_do_not_ask_for_drop_database():
    rules = project.SHOT_RULES
    assert "DROP DATABASE IF EXISTS" not in rules and "CREATE DATABASE IF NOT EXISTS" in rules
    assert "чистый" in rules and "виден на снимке" in rules
