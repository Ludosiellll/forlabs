"""«Своя программа»: папка или ZIP студента → копия в лабе, язык, код для модели."""

import zipfile

import pytest

from forlabs.ingest.own import TEXT_MAX, read_own


def _tree(root, files):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return root


WINFORMS = {
    "Calc/Calc.sln": "Microsoft Visual Studio Solution File",
    "Calc/Calc/Calc.csproj": "<Project ToolsVersion=\"15.0\"></Project>",
    "Calc/Calc/Program.cs": "static class Program { static void Main() { } }",
    "Calc/Calc/Form1.cs": "public partial class Form1 : Form { }",
    "Calc/Calc/Form1.Designer.cs": "partial class Form1 { }",
    "Calc/Calc/Form1.resx": "<root></root>",
    "Calc/Calc/Properties/AssemblyInfo.cs": "[assembly: AssemblyTitle(\"Calc\")]",
    "Calc/Calc/bin/Debug/Calc.exe": b"MZ",
    "Calc/Calc/obj/Debug/x.cache": b"x",
    "Calc/.vs/Calc/v17/.suo": b"x",
    "Calc/Calc/icon.png": b"\x89PNG",
}


def test_folder_is_copied_without_build_output(tmp_path):
    own = read_own(_tree(tmp_path / "src", WINFORMS), tmp_path / "lab" / "own")
    assert own.language == "csharp" and own.engine is None and own.name == "src"
    # общая верхняя папка «Calc» снята, «bin», «obj», «.vs» не взяты
    assert sorted(own.files) == ["Calc/Form1.Designer.cs", "Calc/Form1.cs", "Calc/Form1.resx", "Calc/Program.cs",
                                 "Calc/Properties/AssemblyInfo.cs"]
    assert own.assets == ["Calc/icon.png"]
    assert (tmp_path / "lab" / "own" / "Calc" / "Program.cs").read_text(encoding="utf-8").startswith("static class")
    assert not (tmp_path / "lab" / "own" / "Calc" / "bin").exists()
    assert not list((tmp_path / "lab" / "own").rglob("*.csproj"))
    # код для модели: главный файл первым, ресурсы перечислены по имени
    assert own.text.index("Calc/Program.cs") < own.text.index("Calc/Form1.cs")
    assert "static void Main" in own.text and "icon.png" in own.text


def test_zip_is_read_like_a_folder(tmp_path):
    z = tmp_path / "lab4.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("lab4/main.py", "print('привет')\n")
        f.writestr("lab4/data.csv", "a;b\n1;2\n")
        f.writestr("lab4/__pycache__/main.cpython-312.pyc", b"\0")
    own = read_own(z, tmp_path / "own", name="lab4.zip")
    assert own.name == "lab4.zip" and own.language == "python"
    assert sorted(own.files) == ["data.csv", "main.py"]
    assert not (tmp_path / "own_tmp").exists()  # распаковка — во временную папку, она убрана


def test_unity_project_is_an_engine(tmp_path):
    src = _tree(tmp_path / "Game", {
        "Assets/Scripts/Player.cs": "using UnityEngine;\npublic class Player : MonoBehaviour { }",
        "Assets/Scripts/Player.cs.meta": "guid: 1",
        "ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 2022.3",
        "Library/ArtifactDB": b"x" * 1000,
        "Temp/x.txt": "x",
    })
    own = read_own(src, tmp_path / "own")
    assert own.engine == "Unity" and own.language == "csharp"
    assert own.files == ["Assets/Scripts/Player.cs"]  # настройки проекта и .meta — не код программы


def test_cp1251_code_is_read_for_the_model(tmp_path):
    src = _tree(tmp_path / "p", {"Program.cs": "// Привет\nclass P { static void Main() { } }".encode("cp1251")})
    own = read_own(src, tmp_path / "own")
    assert "// Привет" in own.text


def test_big_program_text_is_cut_but_all_files_are_kept(tmp_path):
    files = {f"Module{i}.py": f"# модуль {i}\n" + "x = 1\n" * 3000 for i in range(8)}
    files["main.py"] = "import Module0\n"
    own = read_own(_tree(tmp_path / "big", files), tmp_path / "own")
    assert len(own.files) == 9
    assert len(own.text) <= TEXT_MAX + 2000
    assert own.text.index("main.py") < own.text.index("Module0.py")
    assert "не показаны" in own.text


def test_folder_without_code_is_refused(tmp_path):
    src = _tree(tmp_path / "pics", {"a.png": b"\x89PNG", "b.jpg": b"\xff\xd8"})
    with pytest.raises(ValueError, match="нет кода"):
        read_own(src, tmp_path / "own")


def test_not_a_folder_or_zip(tmp_path):
    (tmp_path / "x.docx").write_bytes(b"PK")
    with pytest.raises(ValueError, match="папк|ZIP"):
        read_own(tmp_path / "x.docx", tmp_path / "own")


def test_replacing_clears_the_old_copy(tmp_path):
    read_own(_tree(tmp_path / "a", {"main.py": "print(1)"}), tmp_path / "own")
    own = read_own(_tree(tmp_path / "b", {"app.js": "console.log(1)"}), tmp_path / "own")
    assert own.files == ["app.js"] and not (tmp_path / "own" / "main.py").exists()
