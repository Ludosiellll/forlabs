"""Находки ревью: подмена ключей Edge через адрес, чтение файлов вне проекта, потеря прежнего проекта при копии,
обход проверки кода через /*! */ и динамический импорт."""

import pytest

from forlabs.runner import safety
from forlabs.runner.plan import ProjectPlan, validate
from forlabs.runner.shots import browser_url, edge_command

EVIL = 'http://127.0.0.1/" --gpu-launcher="cmd /c calc" --no-sandbox "'


def web(url, files=None):
    return ProjectPlan.model_validate({"language": "web", "kind": "web", "name": "Сайт", "entry": "index.html",
                                       "files": files or [{"path": "index.html", "content": "<h1>Сайт</h1>"}],
                                       "shots": [{"section": 1, "block": 1, "mode": "browser", "url": url}]})


@pytest.mark.parametrize("url", [EVIL, "-x.html", "index.html\" --x", "../../Desktop/secret.pdf",
                                 "C:/Users/x/a.pdf", "file:///C:/Users/x/a.pdf"])
def test_bad_shot_urls_are_problems(url):
    assert validate(web(url)), url


def win_argv(line: str) -> list[str]:
    """Разбор командной строки по правилам CommandLineToArgvW (как её читает Edge)."""
    args, cur, i, quoted, has = [], [], 0, False, False
    while i < len(line):
        c = line[i]
        if c == "\\":
            n = 0
            while i < len(line) and line[i] == "\\":
                n, i = n + 1, i + 1
            if i < len(line) and line[i] == '"':
                cur.append("\\" * (n // 2))
                if n % 2:
                    cur.append('"')
                    i += 1
                continue
            cur.append("\\" * n)
            continue
        if c == '"':
            quoted, has = not quoted, True
        elif c in " \t" and not quoted:
            if cur or has:
                args.append("".join(cur))
            cur, has = [], False
        else:
            cur.append(c)
        i += 1
    if cur or has:
        args.append("".join(cur))
    return args


def test_edge_command_keeps_url_one_argument(tmp_path):
    with pytest.raises(ValueError):
        browser_url(EVIL, "http://127.0.0.1:5000/index.php", tmp_path)  # такой адрес не пропускается вовсе
    argv = win_argv(edge_command(r"C:\Program Files\Edge\msedge.exe", tmp_path / "мой профиль", EVIL))
    assert argv[-2:] == ["--", EVIL]  # даже такой адрес — один аргумент после «--», ключом не станет
    assert not any(a.startswith("--gpu-launcher") for a in argv)


def test_good_urls_are_encoded_and_joined(tmp_path):
    assert browser_url("order.php?id=1", "http://127.0.0.1:5000/index.php", tmp_path) == \
        "http://127.0.0.1:5000/order.php?id=1"
    assert browser_url("/index.html", (tmp_path / "Проект Сайт" / "index.html").as_uri(), tmp_path).endswith(
        "/Проект%20Сайт/index.html".replace("Проект", "%D0%9F%D1%80%D0%BE%D0%B5%D0%BA%D1%82").replace(
            "Сайт", "%D0%A1%D0%B0%D0%B9%D1%82"))


def test_browser_url_never_leaves_project(tmp_path):
    base = (tmp_path / "Проект" / "index.html").as_uri()
    with pytest.raises(ValueError):
        browser_url("../../Desktop/secret.pdf", base, tmp_path)
    with pytest.raises(ValueError):
        browser_url("file:///C:/Users/x/secret.pdf", base, tmp_path)


def sql(text):
    return ProjectPlan.model_validate({"language": "mysql", "kind": "sql", "name": "БД", "entry": "db.sql",
                                       "files": [{"path": "db.sql", "content": text}]})


@pytest.mark.parametrize("text", ["/*!50000 INSTALL PLUGIN x SONAME 'a.dll' */;", "/*!DROP DATABASE mysql*/;",
                                  "SET GLOBAL general_log_file := 'x.bat';", "SET @@global.general_log = 1;",
                                  "SELECT 1 /*!INTO OUTFILE 'C:/x.bat' */;"])
def test_sql_hidden_in_executable_comments_is_caught(text):
    assert safety.scan(sql("CREATE DATABASE shop;\nUSE shop;\n" + text))


@pytest.mark.parametrize("code", ['m = getattr(__builtins__, "__imp" + "ort__")("url" + "lib.request")\n',
                                  'imp = __builtins__.__dict__["__import__"]\n',
                                  'g = globals()["__builtins__"]\n',
                                  'import importlib as il\nm = il.import_module("so" + "cket")\n'])
def test_python_dynamic_import_is_caught(code):
    p = ProjectPlan.model_validate({"language": "python", "kind": "console", "name": "Лаба", "entry": "main.py",
                                    "files": [{"path": "main.py", "content": code + "print(1)\n"}]})
    assert safety.scan(p), code


@pytest.mark.parametrize("expect, seen, typed, ok", [
    # MariaDB не вставила строку, а «Булгаков» на экране только в эхе набранного INSERT — это не результат
    ("Булгаков", "MariaDB [library]> INSERT INTO books VALUES ('Мастер', 'Булг\nаков');\nERROR 1366\nEmpty set",
     "INSERT INTO books VALUES ('Мастер', 'Булгаков');\nSELECT * FROM books;\n", False),
    ("Булгаков", "mysql> INSERT INTO books VALUES ('Мастер', 'Булгаков');\n| 1 | Мастер | Булгаков |",
     "INSERT INTO books VALUES ('Мастер', 'Булгаков');\n", True),
    ("Сумма: 17", "Введите a: 12\nВведите b: 5\nСумма: 17", "12\n5\n", True),
    ("Сумма: 17", "Введите a: 12\nВведите b: 5\nОшибка", "12\n5\n", False),
    ("12", "Введите a: 12\nВы ввели 12", "12\n", True),
    ("12", "Введите a: 12\nОшибка", "12\n", False),
    ("Итого", "Итого: 5", "", True),
])
def test_expect_must_be_in_output_not_only_in_typed_text(expect, seen, typed, ok):
    from forlabs.runner.shots import shown

    assert shown(expect, seen, typed) is ok


def test_java_classpath_is_relative_so_cyrillic_project_path_works(tmp_path):
    """В Windows с кодовой страницей 1252 кириллица в аргументах java превращается в «?»: путь классов — от папки
    проекта, где программа и запускается."""
    from forlabs.runner import languages
    from forlabs.runner.languages import Toolchain

    p = ProjectPlan.model_validate({"language": "java", "kind": "console", "name": "Лаба", "entry": "src/ru/lab/Main.java",
                                    "files": [{"path": "src/ru/lab/Main.java", "content": "package ru.lab;\nclass Main {}\n"}]})
    d = tmp_path / "Проект Иванова"
    spec = languages.run_spec(p, d / "out", d, {"java": Toolchain("java", str(tmp_path / "jdk" / "bin" / "javac"))})
    cp = spec.cmd[spec.cmd.index("-cp") + 1]
    assert cp == "out" and spec.cwd == d and spec.cmd[-1] == "ru.lab.Main"


def test_remove_tree_deletes_read_only_files(tmp_path):
    """MySQL кладёт в папку данных ключи «только для чтения»: в Windows обычный rmtree их не удаляет."""
    import os
    import stat

    from forlabs.core.fs import remove_tree

    d = tmp_path / "mysql-1" / "db"
    d.mkdir(parents=True)
    key = d / "server-key.pem"
    key.write_text("x")
    os.chmod(key, stat.S_IREAD)
    remove_tree(tmp_path / "mysql-1")
    assert not (tmp_path / "mysql-1").exists()
    assert remove_tree(tmp_path / "нет такой")  # нет папки — не ошибка
