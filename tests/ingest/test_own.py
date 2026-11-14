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
    # корень — папка проекта Visual Studio (Calc/Calc), «bin», «obj», «.vs», .sln не взяты
    assert sorted(own.files) == ["Form1.Designer.cs", "Form1.cs", "Form1.resx", "Program.cs",
                                 "Properties/AssemblyInfo.cs"]
    assert own.assets == ["icon.png"] and own.project_name == "Calc"
    assert (tmp_path / "lab" / "own" / "Program.cs").read_text(encoding="utf-8").startswith("static class")
    assert not (tmp_path / "lab" / "own" / "bin").exists()
    assert not list((tmp_path / "lab" / "own").rglob("*.csproj"))
    # код для модели: главный файл первым, ресурсы перечислены по имени
    assert own.text.index("Program.cs") < own.text.index("Form1.cs")
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


# --- по проверке кода ---

def test_flask_app_is_python_not_web(tmp_path):
    own = read_own(_tree(tmp_path / "f", {"app.py": "from flask import Flask", "templates/index.html": "<p>",
                                          "static/style.css": "p{}"}), tmp_path / "own")
    assert own.language == "python"


def test_plain_site_is_web(tmp_path):
    own = read_own(_tree(tmp_path / "s", {"index.html": "<p>", "app.js": "x()", "style.css": "p{}"}), tmp_path / "own")
    assert own.language == "web"


def test_visual_studio_project_folder_becomes_the_root(tmp_path):
    files = dict(WINFORMS)
    files["Calc/Calc/Calc.csproj"] = "<Project><PropertyGroup><RootNamespace>CalcApp</RootNamespace></PropertyGroup></Project>"
    own = read_own(_tree(tmp_path / "src", files), tmp_path / "own")
    assert "Program.cs" in own.files and "Form1.resx" in own.files  # без «Calc/»: ресурсы получат имя студента
    assert own.project_name == "CalcApp"


def test_project_name_from_csproj_file_name(tmp_path):
    own = read_own(_tree(tmp_path / "p", {"Lab3/Lab3.csproj": "<Project/>", "Lab3/Program.cs": "class P {}"}),
                   tmp_path / "own")
    assert own.project_name == "Lab3" and own.files == ["Program.cs"]


def test_ordinary_folders_named_like_unity_ones_are_kept(tmp_path):
    own = read_own(_tree(tmp_path / "lib", {"main.py": "import library.books", "library/books.py": "B = 1",
                                            "logs/parse.py": "x = 1"}), tmp_path / "own")
    assert sorted(own.files) == ["library/books.py", "logs/parse.py", "main.py"]


def test_unity_library_is_not_walked(tmp_path, monkeypatch):
    import forlabs.ingest.own as own_mod

    monkeypatch.setattr(own_mod, "MAX_WALK", 50)
    files = {f"Library/Artifacts/{i}.bin": b"x" for i in range(200)}
    files.update({"Assets/Player.cs": "using UnityEngine;", "ProjectSettings/ProjectVersion.txt": "x"})
    own = read_own(_tree(tmp_path / "G", files), tmp_path / "own")
    assert own.engine == "Unity" and own.files == ["Assets/Player.cs"]


def test_encrypted_or_unsupported_zip_is_a_plain_error(tmp_path, monkeypatch):
    import forlabs.ingest.archive as archive

    z = tmp_path / "p.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("main.py", "print(1)")
    real_open = zipfile.ZipFile.open

    def locked(self, *a, **k):
        raise RuntimeError("File is encrypted, password required for extraction")

    monkeypatch.setattr(zipfile.ZipFile, "open", locked)
    with pytest.raises(ValueError, match="парол|не поддерж"):
        read_own(z, tmp_path / "own")
    monkeypatch.setattr(zipfile.ZipFile, "open", real_open)
    assert archive  # модуль тот же


def test_failed_replace_keeps_the_previous_copy(tmp_path, monkeypatch):
    import shutil as sh

    read_own(_tree(tmp_path / "a", {"main.py": "print(1)"}), tmp_path / "own")
    real = sh.copyfile

    def broken(src, dst, *a, **k):
        raise PermissionError(13, "занят")

    monkeypatch.setattr("forlabs.ingest.own.shutil.copyfile", broken)
    with pytest.raises(OSError):
        read_own(_tree(tmp_path / "b", {"app.py": "print(2)"}), tmp_path / "own")
    monkeypatch.setattr("forlabs.ingest.own.shutil.copyfile", real)
    assert (tmp_path / "own" / "main.py").exists()


def test_generated_files_go_last_in_the_model_text(tmp_path):
    own = read_own(_tree(tmp_path / "w", {"Program.cs": "class P {}", "Form1.cs": "class F {}",
                                          "Form1.Designer.cs": "partial class F {}", "Form1.resx": "<root/>",
                                          "Logic.cs": "class L {}"}), tmp_path / "own")
    t = own.text
    assert t.index("Logic.cs") < t.index("Form1.Designer.cs") and t.index("Logic.cs") < t.index("Form1.resx")


def test_text_with_control_characters_is_not_code(tmp_path):
    own = read_own(_tree(tmp_path / "c", {"main.py": "print(1)", "weird.py": "x = 1\x01\n"}), tmp_path / "own")
    assert own.files == ["main.py"]


def test_fingerprint_changes_with_the_code(tmp_path):
    a = read_own(_tree(tmp_path / "a", {"main.py": "print(1)"}), tmp_path / "own")
    b = read_own(_tree(tmp_path / "b", {"main.py": "print(2)"}), tmp_path / "own")
    assert a.sha and b.sha and a.sha != b.sha
