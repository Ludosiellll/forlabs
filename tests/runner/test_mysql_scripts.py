"""Скрипты SQL лабы проверяются на своём сервере до отчёта, а каждый снимок начинается с чистого сервера."""

import shutil

import pytest

from forlabs.runner import mysqlrun

CLIENT = shutil.which("mysql")
pytestmark = pytest.mark.skipif(not CLIENT, reason="нет mysql")

CREATE = ("CREATE DATABASE IF NOT EXISTS forum_ivanova;\nUSE forum_ivanova;\n"
          "CREATE TABLE users (id INT PRIMARY KEY, name VARCHAR(50));\n"
          "INSERT INTO users VALUES (1, 'Анна'), (2, 'Пётр');\n")
SELECT = "USE forum_ivanova;\nSELECT name FROM users ORDER BY id;\n"
BROKEN = "USE forum_ivanova;\nSELECT name FROM users;\nSELEC * FORM users;\n"


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    try:
        s = mysqlrun.start(CLIENT, tmp_path_factory.mktemp("mysql"), low=False)
    except mysqlrun.MysqlError as e:
        pytest.skip(f"сервер не поднялся здесь: {e}")
    yield s
    s.stop()


def write(tmp_path, name, sql):
    f = tmp_path / name
    f.write_text(sql, encoding="utf-8")
    return f


def test_good_scripts_run_in_order_without_errors(srv, tmp_path):
    srv.reset()
    assert srv.run_file(write(tmp_path, "create.sql", CREATE)) == ""
    assert srv.run_file(write(tmp_path, "select.sql", SELECT)) == ""


def test_error_names_the_file_and_the_line(srv, tmp_path):
    srv.reset()
    srv.run_file(write(tmp_path, "create.sql", CREATE))
    err = srv.run_file(write(tmp_path, "queries.sql", BROKEN))
    assert "queries.sql" in err and "3" in err and "1064" in err  # файл, строка и код ошибки MySQL


def test_reset_gives_a_clean_server_so_scripts_need_no_drop(srv, tmp_path):
    srv.reset()
    f = write(tmp_path, "create.sql", CREATE)
    assert srv.run_file(f) == ""
    code, out = srv.query("CREATE USER 'reader'@'localhost' IDENTIFIED BY 'x'")
    assert code == 0, out
    srv.reset()
    code, out = srv.query("SHOW DATABASES")
    assert code == 0 and "forum_ivanova" not in out
    code, out = srv.query("SELECT COUNT(*) FROM mysql.user WHERE user = 'reader'")
    assert out.strip().splitlines()[-1] == "0"
    assert srv.run_file(f) == ""  # второй раз — без «таблица уже есть» и без DROP в скрипте


def test_check_scripts_reports_the_first_broken_one(srv, tmp_path):
    write(tmp_path, "create.sql", CREATE)
    write(tmp_path, "queries.sql", BROKEN)
    write(tmp_path, "select.sql", SELECT)
    assert mysqlrun.check_scripts(srv, tmp_path, ["create.sql", "select.sql"]) == ""
    log = mysqlrun.check_scripts(srv, tmp_path, ["create.sql", "queries.sql", "select.sql"])
    assert "queries.sql" in log and "select.sql" not in log
    code, out = srv.query("SHOW DATABASES")
    assert "forum_ivanova" not in out  # после проверки сервер снова чистый
