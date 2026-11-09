"""Код модели не выполняется при сборке: имена файлов-опции (@file, --opt), подмена модулей Python, проверка на
опасное — до сборки."""

import shutil
import sys

import pytest

from forlabs.runner import languages
from forlabs.runner.plan import ProjectPlan, validate


def plan(language, files, entry, kind="console"):
    return ProjectPlan.model_validate({"language": language, "kind": kind, "name": "Лаба", "entry": entry,
                                       "files": [{"path": p, "content": c} for p, c in files.items()]})


@pytest.mark.parametrize("bad", ["@opts", "--require=x.js", "-o.cpp", "src/@x.cpp", "src/--x.js"])
def test_option_like_file_names_rejected(bad):
    p = plan("cpp", {"main.cpp": "int main(){return 0;}", bad: "x"}, "main.cpp")
    assert any(bad.split("/")[-1] in pr or "начинаться" in pr for pr in validate(p))


@pytest.mark.skipif(not shutil.which("g++"), reason="нет g++")
def test_gpp_gets_sources_as_paths_not_options(tmp_path):
    p = plan("cpp", {"main.cpp": "int main(){return 0;}"}, "main.cpp")
    tools = {k: v for k, v in languages.detect().items() if k in ("gpp", "clang")}
    d = languages.materialize(p, tmp_path / "p", tools)
    seen = []
    real = languages._run

    def spy(cmd, *a, **k):
        seen.append(cmd)
        return real(cmd, *a, **k)

    languages._run = spy
    try:
        assert languages.build(d, p, tools).ok
    finally:
        languages._run = real
    srcs = [a for a in seen[-1] if a.endswith(".cpp")]
    assert srcs and all(a.startswith("./") for a in srcs)


def test_python_check_is_isolated_from_project_modules(tmp_path):
    marker = tmp_path / "pwned"
    evil = f"open({str(marker)!r}, 'w').write('x')\n"
    p = plan("python", {"main.py": "print(1)\n", "traceback.py": evil, "linecache.py": evil, "ast.py": evil}, "main.py")
    tools = {k: v for k, v in languages.detect().items() if k == "python"}
    if not tools:
        pytest.skip("нет python")
    d = languages.materialize(p, tmp_path / "p", tools)
    languages.build(d, p, tools)
    assert not marker.exists()  # файлы проекта с именами модулей не выполнились при проверке синтаксиса
