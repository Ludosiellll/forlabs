"""Настоящая сборка и запуск консольных программ лаб: ввод с клавиатуры, вывод по-русски, ошибки компиляции."""

import shutil
import sys

import pytest

from forlabs.runner import languages as L
from forlabs.runner.plan import ProjectPlan, validate
from forlabs.runner.safety import scan

TOOLS = L.detect()


def need(key):
    return pytest.mark.skipif(key not in TOOLS, reason=f"нет инструмента {key}")


def plan(lang, files, entry, name="Лабораторная", kind="console"):
    p = ProjectPlan(language=lang, kind=kind, name=name, entry=entry,
                    files=[{"path": k, "content": v} for k, v in files.items()])
    assert validate(p) == [] and scan(p) == []
    return p


def build_and_run(p, tmp_path, stdin, tools=None):
    tools = TOOLS if tools is None else tools
    d = L.materialize(p, tmp_path / "Проект", tools)
    r = L.build(d, p, tools, 600)
    assert r.ok, r.log
    code, out = L.run_console(L.run_spec(p, r.artifact, d, tools), stdin, 60)
    assert code == 0, out
    return d, out


def failing_build(p, tmp_path, tools=None):
    tools = TOOLS if tools is None else tools
    d = L.materialize(p, tmp_path / "Проект", tools)
    r = L.build(d, p, tools, 600)
    assert not r.ok and r.artifact is None
    assert str(d) not in r.log and len(r.log) < 7000, r.log  # пути короткие, лог обрезан
    return r.log


# ---------- C++ ----------

CPP = {
    "utils.h": "#pragma once\n#include <vector>\ndouble average(const std::vector<double>& v);\n",
    "utils.cpp": ('#include "utils.h"\n\ndouble average(const std::vector<double>& v) {\n    double s = 0;\n'
                  '    for (double x : v) s += x;\n    return v.empty() ? 0 : s / v.size();\n}\n'),
    "main.cpp": '''#include <fstream>
#include <iostream>
#include <vector>
#include "utils.h"
#ifdef _WIN32
#include <windows.h>
#endif

int main() {
#ifdef _WIN32
    SetConsoleOutputCP(65001);
    SetConsoleCP(65001);
#endif
    int n;
    std::cout << "Введите количество оценок: ";
    std::cin >> n;
    std::vector<double> marks(n);
    for (auto& m : marks) std::cin >> m;
    std::ofstream("result.txt") << average(marks);
    std::cout << "Средний балл: " << average(marks) << std::endl;
    return 0;
}
''',
}


@pytest.mark.parametrize("compiler", ["gpp", "clang"])
def test_cpp_build_and_run(tmp_path, compiler):
    if compiler not in TOOLS:
        pytest.skip(f"нет {compiler}")
    d, out = build_and_run(plan("cpp", CPP, "main.cpp", "Средний балл"), tmp_path, "3\n7 8 9\n",
                           {compiler: TOOLS[compiler]})
    assert "Средний балл: 8" in out
    assert (d / "result.txt").read_text().strip() == "8"


@need("gpp")
def test_cpp_compile_error_is_short_and_readable(tmp_path):
    bad = dict(CPP, **{"main.cpp": '#include <iostream>\nint main() {\n    std::cout << "Привет" << x;\n}\n'})
    log = failing_build(plan("cpp", bad, "main.cpp"), tmp_path, {"gpp": TOOLS["gpp"]})
    assert "main.cpp:3" in log and "error" in log and "'x'" in log


# ---------- Python ----------

PY = {"main.py": '''from pathlib import Path

name = input("Введите имя студента: ")
marks = list(map(int, input("Введите оценки через пробел: ").split()))
avg = sum(marks) / len(marks)
print(f"Студент {name}: средний балл {avg:.1f}")
Path("results.txt").write_text(f"{name};{avg:.1f}\\n", encoding="utf-8")
'''}


@need("python")
def test_python_run(tmp_path):
    d, out = build_and_run(plan("python", PY, "main.py"), tmp_path, "Иван\n7 8 9\n")
    assert "Студент Иван: средний балл 8.0" in out
    assert (d / "results.txt").read_text(encoding="utf-8") == "Иван;8.0\n"
    assert not (d / "__pycache__").exists()  # проверка синтаксиса не мусорит в папке студента


@need("python")
def test_python_syntax_error(tmp_path):
    log = failing_build(plan("python", {"main.py": 'print("Привет"\nx = 1\n'}, "main.py"), tmp_path)
    assert "main.py" in log and "SyntaxError" in log


# ---------- Java ----------

JAVA = {
    "src/ru/lab/Main.java": '''package ru.lab;

import java.util.Scanner;

public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        System.out.print("Введите имя: ");
        String name = in.nextLine();
        System.out.print("Введите возраст: ");
        int age = in.nextInt();
        Student s = new Student(name, age);
        System.out.println("Создан студент: " + s);
    }
}
''',
    "src/ru/lab/Student.java": '''package ru.lab;

public class Student {
    private final String name;
    private final int age;

    public Student(String name, int age) {
        this.name = name;
        this.age = age;
    }

    @Override
    public String toString() {
        return name + ", " + age + " лет";
    }
}
''',
}


@need("java")
def test_java_build_and_run(tmp_path):
    _, out = build_and_run(plan("java", JAVA, "src/ru/lab/Main.java"), tmp_path, "Мария\n19\n")
    assert "Создан студент: Мария, 19 лет" in out


@need("java")
def test_java_compile_error(tmp_path):
    bad = dict(JAVA, **{"src/ru/lab/Student.java": "package ru.lab;\npublic class Student {\n  int x = ;\n}\n"})
    log = failing_build(plan("java", bad, "src/ru/lab/Main.java"), tmp_path)
    assert "Student.java:3" in log and "error" in log and "JAVA_TOOL_OPTIONS" not in log


# ---------- JavaScript (Node) ----------

JS = {
    "convert.js": "function toFahrenheit(c) {\n  return c * 9 / 5 + 32;\n}\n\nmodule.exports = { toFahrenheit };\n",
    "main.js": '''const readline = require("node:readline");
const { toFahrenheit } = require("./convert");

async function main() {
  const rl = readline.createInterface({ input: process.stdin });
  const lines = rl[Symbol.asyncIterator]();
  process.stdout.write("Введите температуру в градусах Цельсия: ");
  const c = Number((await lines.next()).value);
  console.log(`\\nПо Фаренгейту: ${toFahrenheit(c).toFixed(1)}`);
  rl.close();
}

main();
''',
}


@need("node")
def test_node_run(tmp_path):
    _, out = build_and_run(plan("javascript", JS, "main.js"), tmp_path, "25\n")
    assert "По Фаренгейту: 77.0" in out


@need("node")
def test_node_syntax_error(tmp_path):
    log = failing_build(plan("javascript", dict(JS, **{"convert.js": "function ( {\n"}), "main.js"), tmp_path)
    assert "convert.js" in log and "SyntaxError" in log


# ---------- PHP ----------

PHP = {
    "functions.php": "<?php\nfunction area(float $a, float $b): float\n{\n    return $a * $b;\n}\n",
    "main.php": '''<?php
require_once __DIR__ . "/functions.php";

echo "Введите длину и ширину прямоугольника: ";
[$a, $b] = array_map("floatval", preg_split('/\\s+/', trim(fgets(STDIN))));
echo "Площадь прямоугольника: " . area($a, $b) . PHP_EOL;
''',
}


@need("php")
def test_php_run(tmp_path):
    _, out = build_and_run(plan("php", PHP, "main.php"), tmp_path, "3 4\n")
    assert "Площадь прямоугольника: 12" in out


@need("php")
def test_php_syntax_error(tmp_path):
    log = failing_build(plan("php", dict(PHP, **{"functions.php": "<?php\nfunction area( {\n"}), "main.php"), tmp_path)
    assert "functions.php" in log and "line 2" in log and "No syntax errors" not in log


# ---------- Go ----------

GO = {
    "util.go": '''package main

func reverse(s string) string {
	r := []rune(s)
	for i, j := 0, len(r)-1; i < j; i, j = i+1, j-1 {
		r[i], r[j] = r[j], r[i]
	}
	return string(r)
}
''',
    "main.go": '''package main

import (
	"bufio"
	"fmt"
	"os"
	"strings"
)

func main() {
	reader := bufio.NewReader(os.Stdin)
	fmt.Print("Введите слово: ")
	w, _ := reader.ReadString('\\n')
	w = strings.TrimSpace(w)
	fmt.Printf("Слово «%s» наоборот: %s\\n", w, reverse(w))
}
''',
}


@need("go")
def test_go_build_and_run(tmp_path):
    _, out = build_and_run(plan("go", GO, "main.go", "Обратное слово"), tmp_path, "привет\n")
    assert "Слово «привет» наоборот: тевирп" in out


@need("go")
def test_go_compile_error(tmp_path):
    log = failing_build(plan("go", dict(GO, **{"util.go": "package main\n\nfunc reverse(s string) string {\n\treturn x\n}\n"}),
                             "main.go"), tmp_path)
    assert "util.go:4" in log and "undefined: x" in log


# ---------- C# ----------

CS = {"Program.cs": '''using System;
using System.IO;
using System.Text;

namespace Shop
{
    internal class Program
    {
        private static void Main()
        {
            Console.OutputEncoding = Encoding.UTF8;
            Console.Write("Введите цену товара: ");
            decimal price = decimal.Parse(Console.ReadLine(), System.Globalization.CultureInfo.InvariantCulture);
            string rules = File.ReadAllText("discount.txt").Trim();
            Console.WriteLine($"Цена со скидкой {rules}%: {price * (100 - int.Parse(rules)) / 100:F2}");
        }
    }
}
''', "discount.txt": "10"}


@need("dotnet")
def test_csharp_build_and_run(tmp_path):
    _, out = build_and_run(plan("csharp", CS, "Program.cs", "Магазин"), tmp_path, "200\n")
    assert "Цена со скидкой 10%: 180.00" in out or "Цена со скидкой 10%: 180,00" in out


@need("dotnet")
def test_csharp_compile_error(tmp_path):
    bad = {"Program.cs": 'class Program {\n    static void Main() {\n        int x = "строка";\n    }\n}\n'}
    log = failing_build(plan("csharp", bad, "Program.cs"), tmp_path)
    assert "Program.cs(3," in log and "CS0029" in log and ".csproj]" not in log


# ---------- данные без сборки ----------

def test_mysql_needs_no_build(tmp_path):
    p = plan("mysql", {"library.sql": "CREATE DATABASE library;\nUSE library;\n"}, "library.sql", kind="sql")
    d = L.materialize(p, tmp_path / "m")
    r = L.build(d, p, {}, 30)
    assert r.ok and r.artifact == d / "library.sql"
    if "mysql" in TOOLS:
        s = L.run_spec(p, r.artifact, d, TOOLS)
        assert s.console and "--default-character-set=utf8mb4" in s.cmd


@pytest.mark.skipif(shutil.which("g++") is None or sys.platform == "win32", reason="нужен g++ (Linux)")
def test_cpp_hanging_program_is_stopped(tmp_path):
    p = plan("cpp", {"main.cpp": '#include <iostream>\nint main() {\n    std::cout << "Жду" << std::endl;\n'
                                  '    while (true) {}\n}\n'}, "main.cpp")
    d = L.materialize(p, tmp_path / "h", TOOLS)
    r = L.build(d, p, {"gpp": TOOLS["gpp"]}, 300)
    assert r.ok, r.log
    code, out = L.run_console(L.run_spec(p, r.artifact, d, TOOLS), "", 2)
    assert code is None and "Жду" in out
