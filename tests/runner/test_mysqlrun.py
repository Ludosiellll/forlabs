"""Свой сервер MySQL/MariaDB для снимков: чистая папка, только 127.0.0.1, файлы — только в свою папку, всё убирается."""

import shutil
import subprocess

import pytest

from forlabs.runner import mysqlrun

CLIENT = shutil.which("mysql")
pytestmark = pytest.mark.skipif(not CLIENT, reason="нет mysql")


def test_private_server_clean_limited_and_removed(tmp_path):
    try:
        srv = mysqlrun.start(CLIENT, tmp_path, low=False)
    except mysqlrun.MysqlError as e:
        pytest.skip(f"сервер не поднялся здесь: {e}")
    try:
        sql = ("CREATE DATABASE shop; USE shop; CREATE TABLE t (n INT, title VARCHAR(50)); "
               "INSERT INTO t VALUES (17, 'Мастер и Маргарита'); SELECT CONCAT(title, ' — ', n) FROM t;")
        r = subprocess.run([*srv.client(), "-N", "-e", sql], capture_output=True, text=True, timeout=180)
        # кириллица в таблице без указания кодировки: у MariaDB (XAMPP) по умолчанию latin1
        assert r.returncode == 0 and "Мастер и Маргарита — 17" in r.stdout, r.stderr
        outside = tmp_path / "outside.txt"
        r = subprocess.run([*srv.client(), "-e", f"SELECT 1 INTO OUTFILE '{outside.as_posix()}'"], capture_output=True,
                           text=True, timeout=180)
        assert r.returncode != 0 and not outside.exists()  # писать файлы вне своей папки сервер не может
    finally:
        srv.stop()
    assert not srv.data.exists(), [str(p) for p in srv.data.rglob("*")][:10]  # папка данных удалена


def test_project_stage_server_cleans_its_folder():
    from forlabs.pipeline import project

    try:
        srv = project.start_mysql(CLIENT)
    except mysqlrun.MysqlError as e:
        pytest.skip(f"сервер не поднялся здесь: {e}")
    root = srv.root
    assert root.is_dir()
    r = subprocess.run([*srv.client(), "-N", "-e", "SELECT 1+1"], capture_output=True, text=True, timeout=180)
    assert r.stdout.strip() == "2", r.stderr
    srv.stop()
    assert not root.exists()
