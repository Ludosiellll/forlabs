"""Проверка на Windows-сборщике (CI): какие компиляторы есть, снимаются ли окна программ (PrintWindow),
работает ли автоматизация окон (UI Automation), ввод в консоль, запуск с пониженными правами, Edge, MySQL.
Результат: build/probe/report.json и снимки build/probe/shots/*.png."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
OUT = Path("build/probe")
SHOTS = OUT / "shots"
WORK = Path(os.environ.get("RUNNER_TEMP", "build")) / "probe-work"
REPORT: dict = {"tools": {}, "cases": {}}


def run(cmd, cwd=None, timeout=300, shell=False):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=shell,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout + r.stderr)[-3000:]
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def case(name):
    def deco(fn):
        def wrapper():
            t = time.time()
            try:
                res = fn() or {}
                res.setdefault("ok", True)
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2500:]}
            res["sec"] = round(time.time() - t, 1)
            REPORT["cases"][name] = res
            print(f"[{name}] {'OK' if res.get('ok') else 'FAIL'} {res.get('error', '')}", flush=True)
        return wrapper
    return deco


def tools():
    probes = {
        "dotnet": ["dotnet", "--list-sdks"], "gpp": ["g++", "--version"], "gcc": ["gcc", "--version"],
        "python": [sys.executable, "--version"], "java": ["java", "-version"], "javac": ["javac", "-version"],
        "node": ["node", "--version"], "php": ["php", "-v"], "go": ["go", "version"],
        "mysql": ["mysql", "--version"], "clang": ["clang", "--version"],
        "vswhere": [r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe", "-latest", "-property",
                    "installationPath"],
        "edge": ["powershell", "-NoProfile", "-Command",
                 "(Get-Item 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe').VersionInfo.ProductVersion"],
        "mysql_services": ["powershell", "-NoProfile", "-Command", "Get-Service *mysql* | Format-List Name,Status"],
        "session": ["powershell", "-NoProfile", "-Command",
                    "[Environment]::UserInteractive; (Get-Process -Id $PID).SessionId; whoami"],
    }
    for k, cmd in probes.items():
        code, out = run(cmd, timeout=60)
        REPORT["tools"][k] = {"code": code, "out": out.strip()[:600]}
        print(f"tool {k}: {code} {out.strip()[:120]!r}", flush=True)


def save(img, name):
    from forlabs.runner import winapi

    SHOTS.mkdir(parents=True, exist_ok=True)
    p = SHOTS / f"{name}.png"
    img.save(p)
    lo, hi = img.convert("L").getextrema()
    return {"shot": p.name, "size": img.size, "blank": winapi.blank(img), "range": [lo, hi]}


FORM_CS = textwrap.dedent('''
    using System;
    using System.Windows.Forms;
    namespace ProbeForms {
      public class MainForm : Form {
        TextBox inputA = new TextBox { Name = "inputA", Left = 20, Top = 20, Width = 120 };
        TextBox inputB = new TextBox { Name = "inputB", Left = 160, Top = 20, Width = 120 };
        ComboBox op = new ComboBox { Name = "op", Left = 20, Top = 60, Width = 260, DropDownStyle = ComboBoxStyle.DropDownList };
        Button calc = new Button { Name = "calc", Text = "Вычислить", Left = 20, Top = 100, Width = 120 };
        Label result = new Label { Name = "result", Text = "Результат:", Left = 160, Top = 105, Width = 200 };
        public MainForm() {
          Text = "Калькулятор"; Width = 340; Height = 200;
          op.Items.AddRange(new object[] { "Сумма", "Разность" });
          op.SelectedIndex = 0;
          calc.Click += (s, e) => {
            double a = double.Parse(inputA.Text), b = double.Parse(inputB.Text);
            result.Text = "Результат: " + (op.SelectedIndex == 0 ? a + b : a - b);
          };
          Controls.AddRange(new Control[] { inputA, inputB, op, calc, result });
          Label probe = new Label { Name = "probe", Left = 20, Top = 140, Width = 1, Height = 1 };
          try { System.IO.File.WriteAllText(System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "forlabs-probe.txt"), "x"); probe.Text = "docs-write-ok"; }
          catch (Exception) { probe.Text = "docs-write-denied"; }
          Controls.Add(probe);
        }
        [STAThread] static void Main() { Application.EnableVisualStyles(); Application.Run(new MainForm()); }
      }
    }
''')

CSPROJ_WIN = textwrap.dedent('''
    <Project Sdk="Microsoft.NET.Sdk">
      <PropertyGroup>
        <OutputType>WinExe</OutputType><TargetFramework>{tfm}-windows</TargetFramework>
        <UseWindowsForms>true</UseWindowsForms><Nullable>disable</Nullable><ImplicitUsings>disable</ImplicitUsings>
      </PropertyGroup>
    </Project>
''')

CONSOLE_CS = textwrap.dedent('''
    using System;
    class Program {
      static void Main() {
        Console.Write("Введите первое число: "); double a = double.Parse(Console.ReadLine());
        Console.Write("Введите второе число: "); double b = double.Parse(Console.ReadLine());
        Console.WriteLine("Сумма: " + (a + b));
        Console.WriteLine("Нажмите любую клавишу...");
        Console.ReadKey();
      }
    }
''')

CSPROJ_CON = textwrap.dedent('''
    <Project Sdk="Microsoft.NET.Sdk">
      <PropertyGroup>
        <OutputType>Exe</OutputType><TargetFramework>{tfm}</TargetFramework>
        <Nullable>disable</Nullable><ImplicitUsings>disable</ImplicitUsings>
      </PropertyGroup>
    </Project>
''')


def tfm():
    code, out = run(["dotnet", "--list-sdks"])
    majors = sorted({int(line.split(".")[0]) for line in out.splitlines() if line[:1].isdigit()})
    return f"net{majors[-1]}.0" if majors else "net8.0"


def build_cs(name, src, csproj):
    d = WORK / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "Program.cs").write_text(src, encoding="utf-8")
    (d / f"{name}.csproj").write_text(csproj.format(tfm=tfm()), encoding="utf-8")
    code, out = run(["dotnet", "build", "-c", "Release", "-o", str(d / "out"), "-nologo", "-v", "q"], cwd=d, timeout=600)
    if code != 0:
        raise RuntimeError(f"dotnet build: {out}")
    return d / "out" / f"{name}.exe"


def uia_fill(hwnd):
    from forlabs.runner import uia

    uia.perform(hwnd, [{"action": "set", "target": "inputA", "value": "12"},
                       {"action": "set", "target": "inputB", "value": "5"},
                       {"action": "select", "target": "op", "value": "Сумма"},
                       {"action": "click", "target": "Вычислить"}])
    time.sleep(0.5)
    win = uia.root(hwnd)
    found = uia.texts(win)
    return " | ".join(t for t in found if "Результат" in t or "docs-write" in t)


def _old_uia_fill(hwnd):
    import clr  # pythonnet

    for asm in ("UIAutomationClient", "UIAutomationTypes"):
        try:
            clr.AddReference(f"{asm}, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35")
        except Exception:  # noqa: BLE001
            import glob
            path = (glob.glob(rf"C:\Windows\Microsoft.NET\assembly\GAC_MSIL\{asm}\*\{asm}.dll")
                    + glob.glob(rf"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\WPF\{asm}.dll"))[0]
            clr.AddReference(path)
    from System import IntPtr
    from System.Windows.Automation import (AutomationElement, InvokePattern, PropertyCondition, TreeScope,
                                           ValuePattern)

    root = AutomationElement.FromHandle(IntPtr(hwnd))

    def by_id(i):
        el = root.FindFirst(TreeScope.Descendants, PropertyCondition(AutomationElement.AutomationIdProperty, i))
        if el is None:
            raise RuntimeError(f"нет элемента {i}")
        return el

    by_id("inputA").GetCurrentPattern(ValuePattern.Pattern).SetValue("12")
    by_id("inputB").GetCurrentPattern(ValuePattern.Pattern).SetValue("5")
    by_id("calc").GetCurrentPattern(InvokePattern.Pattern).Invoke()
    time.sleep(0.5)
    return by_id("result").Current.Name


def forms_case(low):
    from forlabs.runner import winapi

    exe = build_cs("ProbeFormsLow" if low else "ProbeForms", FORM_CS.replace("ProbeForms", "ProbeFormsLow" if low else "ProbeForms"), CSPROJ_WIN)
    cwd = exe.parent
    if low:
        winapi.low_label_dir(str(cwd))
    app = winapi.spawn(f'"{exe}"', str(cwd), low=low)
    try:
        w = winapi.wait_window({app.pid}, 40)
        if not w:
            raise RuntimeError(f"окно не появилось (жив={app.alive()})")
        time.sleep(1.0)
        res = {"title": w.title, "class": w.cls}
        res["before"] = save(winapi.capture(w.hwnd), f"forms{'_low' if low else ''}_before")
        res["result_text"] = uia_fill(w.hwnd)
        time.sleep(0.5)
        res["after"] = save(winapi.capture(w.hwnd), f"forms{'_low' if low else ''}_after")
        res["ok"] = "17" in res["result_text"] and not res["after"]["blank"]
        return res
    finally:
        app.close()


@case("winforms_normal")
def c1():
    return forms_case(low=False)


@case("winforms_low")
def c2():
    return forms_case(low=True)


def type_into_console(pid, text, title=None):
    from forlabs.runner import console

    res = console.act(pid, text=text, title=title)
    if not res.get("ok"):
        raise RuntimeError(f"ввод в консоль: {res}")


def _old_type_into_console(pid, text):
    helper = textwrap.dedent(f'''
        import time, win32console, win32file, win32con
        win32console.FreeConsole()
        win32console.AttachConsole({pid})
        h = win32file.CreateFile("CONIN$", win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                                 win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE, None, win32con.OPEN_EXISTING, 0, None)
        con = win32console.PyConsoleScreenBufferType(h)
        recs = []
        for ch in {text!r}:
            for down in (True, False):
                r = win32console.PyINPUT_RECORDType(win32console.KEY_EVENT)
                r.KeyDown = down; r.RepeatCount = 1
                if ch == "\\n":
                    r.Char = "\\r"; r.VirtualKeyCode = 13
                else:
                    r.Char = ch
                recs.append(r)
        con.WriteConsoleInput(recs)
        time.sleep(0.3)
        win32console.FreeConsole()
    ''')
    code, out = run([sys.executable, "-c", helper], timeout=30)
    if code != 0:
        raise RuntimeError(f"ввод в консоль: {out}")


def console_case(name, cmd, cwd, inputs, low=False, expect=None, conhost=True, title=None):
    from forlabs.runner import console, winapi

    full = (f'"{console.CONHOST}" cmd.exe /d /s /c "chcp 65001>nul & {cmd} & pause>nul"' if conhost else cmd)
    app = winapi.spawn(full, str(cwd), low=low, new_console=not conhost)
    try:
        w = winapi.wait_window({app.pid}, 30, lambda x: x.cls in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"))
        if not w:
            raise RuntimeError(f"окно консоли не появилось (жив={app.alive()})")
        time.sleep(1.5)
        tree = winapi.process_tree({app.pid})
        target = next((p for p in sorted(tree) if p != app.pid), app.pid)
        type_into_console(target if conhost else app.pid, inputs, title=title or str(Path(cwd) / "program.exe"))
        time.sleep(1.5)
        screen = console.act(target, read=True).get("screen", "")
        res = {"title": winapi.windows_of({w.pid})[0].title if winapi.windows_of({w.pid}) else w.title,
               "class": w.cls, "screen": screen[-400:]}
        if expect and expect not in screen:
            res["ok"] = False
            res["error"] = f"в консоли нет «{expect}»"
        res["shot"] = save(winapi.capture(w.hwnd), name)
        res.setdefault("ok", not res["shot"]["blank"])
        return res
    finally:
        app.close()


@case("console_cs")
def c3():
    exe = build_cs("ProbeConsole", CONSOLE_CS, CSPROJ_CON)
    return console_case("console_cs", f'"{exe}"', exe.parent, "12\n5\n", expect="Сумма: 17", title=str(exe))


@case("console_cs_low")
def c3b():
    exe = build_cs("ProbeConsole2", CONSOLE_CS, CSPROJ_CON)
    from forlabs.runner import winapi
    winapi.low_label_dir(str(exe.parent))
    return console_case("console_cs_low", f'"{exe}"', exe.parent, "12\n5\n", low=True, expect="Сумма: 17", title=str(exe))


@case("console_python")
def c4():
    d = WORK / "py"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text('a = float(input("Введите a: "))\nb = float(input("Введите b: "))\n'
                               'print("Сумма:", a + b)\ninput("Нажмите Enter...")\n', encoding="utf-8")
    return console_case("console_python", f'"{sys.executable}" main.py', d, "12\n5\n")


@case("console_cpp_gpp")
def c5():
    d = WORK / "cpp"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.cpp").write_text(textwrap.dedent('''
        #include <iostream>
        #include <windows.h>
        int main() {
          SetConsoleOutputCP(65001); SetConsoleCP(65001);
          double a, b; std::cout << "Введите два числа: "; std::cin >> a >> b;
          std::cout << "Сумма: " << a + b << std::endl; std::cin.get(); std::cin.get(); return 0; }
    '''), encoding="utf-8")
    code, out = run(["g++", "-O2", "-static", "-o", "main.exe", "main.cpp"], cwd=d, timeout=300)
    if code:
        raise RuntimeError(out)
    return console_case("console_cpp_gpp", '"main.exe"', d, "12 5\n")


@case("console_java")
def c6():
    d = WORK / "java"
    d.mkdir(parents=True, exist_ok=True)
    (d / "Main.java").write_text(textwrap.dedent('''
        import java.util.Scanner;
        public class Main { public static void main(String[] a) {
          Scanner s = new Scanner(System.in); System.out.print("Введите два числа: ");
          double x = s.nextDouble(), y = s.nextDouble(); System.out.println("Сумма: " + (x + y)); s.nextLine(); s.nextLine(); } }
    '''), encoding="utf-8")
    code, out = run(["javac", "-encoding", "UTF-8", "Main.java"], cwd=d)
    if code:
        raise RuntimeError(out)
    return console_case("console_java", "java -Dstdout.encoding=UTF-8 -Dfile.encoding=UTF-8 Main", d, "12 5\n")


@case("console_node")
def c7():
    d = WORK / "node"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.js").write_text(textwrap.dedent('''
        const rl = require("readline").createInterface({ input: process.stdin, output: process.stdout });
        rl.question("Введите a: ", a => rl.question("Введите b: ", b => { console.log("Сумма:", +a + +b);
          rl.question("Enter...", () => rl.close()); }));
    '''), encoding="utf-8")
    return console_case("console_node", "node main.js", d, "12\n5\n")


@case("console_go")
def c8():
    d = WORK / "go"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.go").write_text('package main\nimport "fmt"\nfunc main() { var a, b float64\n'
                               'fmt.Print("Введите два числа: "); fmt.Scan(&a, &b)\n'
                               'fmt.Println("Сумма:", a+b); var s string; fmt.Scanln(&s); fmt.Scanln(&s) }\n',
                               encoding="utf-8")
    code, out = run(["go", "build", "-o", "main.exe", "main.go"], cwd=d, timeout=300)
    if code:
        raise RuntimeError(out)
    return console_case("console_go", '"main.exe"', d, "12 5\n")


@case("console_php")
def c9():
    d = WORK / "php"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.php").write_text('<?php\necho "Введите a: "; $a = (float)trim(fgets(STDIN));\n'
                                'echo "Введите b: "; $b = (float)trim(fgets(STDIN));\necho "Сумма: ".($a+$b)."\\n";'
                                ' fgets(STDIN);\n', encoding="utf-8")
    return console_case("console_php", "php main.php", d, "12\n5\n")


@case("edge_window")
def c10():
    from forlabs.runner import winapi

    d = WORK / "web"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text('<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Кофейня «Зерно»</title>'
                                  '<style>body{font-family:Segoe UI;background:#f4efe6;margin:40px}h1{color:#6b3e26}'
                                  '</style></head><body><h1>Кофейня «Зерно»</h1><p>Меню и цены</p></body></html>',
                                  encoding="utf-8")
    prof = WORK / "edge-profile"
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    cmd = (f'"{edge}" --user-data-dir="{prof}" --no-first-run --no-default-browser-check '
           f'--disable-features=msEdgeFirstRunExperience,msImplicitSignin --window-size=1100,700 --new-window '
           f'"{(d / "index.html").as_uri()}"')
    app = winapi.spawn(cmd, str(d), low=False, max_processes=64, memory_mb=4096)
    try:
        w = winapi.wait_window({app.pid}, 40, lambda x: "Зерно" in x.title)
        if not w:
            raise RuntimeError("окно Edge с сайтом не появилось")
        time.sleep(2.5)
        res = {"title": w.title, "class": w.cls}
        res["shot"] = save(winapi.capture(w.hwnd), "edge_window")
        res["ok"] = not res["shot"]["blank"]
        return res
    finally:
        app.close()


@case("mysql")
def c11():
    mysql = shutil.which("mysql")
    bindir = Path(mysql).parent
    data = WORK / "mysql-data"
    shutil.rmtree(data, ignore_errors=True)
    code, out = run([str(bindir / "mysqld.exe"), "--initialize-insecure", f"--datadir={data}"], timeout=300)
    res = {"init": [code, out[-600:]]}
    srv = subprocess.Popen([str(bindir / "mysqld.exe"), f"--datadir={data}", "--port=3306", "--console"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        code, out = run(["mysql", "-u", "root", "-e", "SELECT VERSION();"], timeout=20)
        if code == 0:
            break
        time.sleep(1)
    res["select"] = [code, out[-300:]]
    sql = ("CREATE DATABASE library; USE library; CREATE TABLE books (id INT PRIMARY KEY AUTO_INCREMENT, title VARCHAR(100), "
           "author VARCHAR(100)); INSERT INTO books (title, author) VALUES ('Мастер и Маргарита','Булгаков'),('Война и мир','Толстой');\n"
           "SELECT * FROM books;\n")
    try:
        res["console"] = console_case("mysql_console", "mysql -u root --default-character-set=utf8mb4", WORK, sql)
        res["ok"] = res["console"]["ok"] and code == 0
    finally:
        srv.terminate()
    return res


MSGBOX_CS = FORM_CS.replace('result.Text = "Результат: " + (op.SelectedIndex == 0 ? a + b : a - b);',
                            'MessageBox.Show("Сумма равна " + (a + b), "Результат");').replace("ProbeForms", "ProbeMsg")


@case("winforms_messagebox")
def c12():
    from forlabs.runner import uia, winapi

    exe = build_cs("ProbeMsg", MSGBOX_CS, CSPROJ_WIN)
    app = winapi.spawn(f'"{exe}"', str(exe.parent), low=False)
    try:
        w = winapi.wait_window({app.pid}, 40)
        uia.perform(w.hwnd, [{"action": "set", "target": "inputA", "value": "12"},
                             {"action": "set", "target": "inputB", "value": "5"},
                             {"action": "click", "target": "calc"}])
        box = winapi.wait_window({app.pid}, 10, lambda x: x.hwnd != w.hwnd, owned=True)
        if not box:
            raise RuntimeError("окно сообщения не появилось")
        res = {"box_title": box.title, "box_class": box.cls}
        res["shot"] = save(winapi.capture(box.hwnd), "winforms_messagebox")
        res["ok"] = box.title == "Результат" and not res["shot"]["blank"]
        return res
    finally:
        app.close()


WPF_XAML = textwrap.dedent('''
    <Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
            xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" x:Class="ProbeWpf.MainWindow"
            Title="Учёт книг" Width="360" Height="220">
      <StackPanel Margin="12">
        <TextBox x:Name="title" Margin="0,0,0,8"/>
        <Button x:Name="add" Content="Добавить" Click="Add_Click" Width="120" HorizontalAlignment="Left"/>
        <ListBox x:Name="books" Height="80" Margin="0,8,0,0"/>
      </StackPanel>
    </Window>
''')
WPF_CS = textwrap.dedent('''
    using System.Windows;
    namespace ProbeWpf {
      public partial class MainWindow : Window {
        public MainWindow() { InitializeComponent(); }
        void Add_Click(object s, RoutedEventArgs e) { books.Items.Add(title.Text); title.Clear(); }
      }
      public partial class App : Application {
        [System.STAThread] public static void Main() { new App().Run(new MainWindow()); }
      }
    }
''')


@case("wpf")
def c13():
    from forlabs.runner import uia, winapi

    d = WORK / "ProbeWpf"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "MainWindow.xaml").write_text(WPF_XAML, encoding="utf-8")
    (d / "MainWindow.xaml.cs").write_text(WPF_CS, encoding="utf-8")
    (d / "ProbeWpf.csproj").write_text(textwrap.dedent(f'''
        <Project Sdk="Microsoft.NET.Sdk">
          <PropertyGroup><OutputType>WinExe</OutputType><TargetFramework>{tfm()}-windows</TargetFramework>
            <UseWPF>true</UseWPF><StartupObject>ProbeWpf.App</StartupObject><EnableDefaultApplicationDefinition>false</EnableDefaultApplicationDefinition></PropertyGroup>
        </Project>'''), encoding="utf-8")
    code, out = run(["dotnet", "build", "-c", "Release", "-o", str(d / "out"), "-nologo", "-v", "q"], cwd=d, timeout=600)
    if code:
        raise RuntimeError(out)
    exe = d / "out" / "ProbeWpf.exe"
    app = winapi.spawn(f'"{exe}"', str(exe.parent), low=False)
    try:
        w = winapi.wait_window({app.pid}, 40)
        uia.perform(w.hwnd, [{"action": "set", "target": "title", "value": "Мастер и Маргарита"},
                             {"action": "click", "target": "add"},
                             {"action": "set", "target": "title", "value": "Война и мир"},
                             {"action": "click", "target": "add"}])
        time.sleep(0.5)
        found = uia.texts(uia.root(w.hwnd))
        res = {"texts": found[:20]}
        res["shot"] = save(winapi.capture(w.hwnd), "wpf")
        res["ok"] = "Война и мир" in found and not res["shot"]["blank"]
        return res
    finally:
        app.close()


def keyboard_case(name, cmd, cwd, keys, title_has, low=False):
    from forlabs.runner import winapi

    app = winapi.spawn(cmd, str(cwd), low=low)
    try:
        w = winapi.wait_window({app.pid}, 40, lambda x: title_has in x.title)
        if not w:
            raise RuntimeError(f"окно «{title_has}» не появилось (жив={app.alive()})")
        time.sleep(1.5)
        for k in keys:
            if k.startswith("key:"):
                winapi.press(w.hwnd, k[4:])
            else:
                winapi.type_text(w.hwnd, k)
            time.sleep(0.2)
        time.sleep(0.8)
        res = {"title": w.title}
        res["shot"] = save(winapi.capture(w.hwnd), name)
        res["ok"] = not res["shot"]["blank"]
        return res
    finally:
        app.close()


@case("java_swing_keyboard")
def c14():
    d = WORK / "swing"
    d.mkdir(parents=True, exist_ok=True)
    (d / "Main.java").write_text(textwrap.dedent('''
        import javax.swing.*; import java.awt.*;
        public class Main { public static void main(String[] args) { SwingUtilities.invokeLater(() -> {
          JFrame f = new JFrame("Калькулятор"); f.setLayout(new FlowLayout());
          JTextField a = new JTextField(6), b = new JTextField(6); JButton c = new JButton("Вычислить"); JLabel r = new JLabel("Результат:");
          c.addActionListener(e -> r.setText("Результат: " + (Double.parseDouble(a.getText()) + Double.parseDouble(b.getText()))));
          f.add(a); f.add(b); f.add(c); f.add(r); f.getRootPane().setDefaultButton(c);
          f.setSize(420, 120); f.setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE); f.setVisible(true); a.requestFocusInWindow(); }); } }
    '''), encoding="utf-8")
    code, out = run(["javac", "-encoding", "UTF-8", "Main.java"], cwd=d)
    if code:
        raise RuntimeError(out)
    return keyboard_case("java_swing", "javaw -Dfile.encoding=UTF-8 Main", d, ["12", "key:tab", "5", "key:enter"],
                         "Калькулятор")


@case("python_tkinter_keyboard")
def c15():
    d = WORK / "tk"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(textwrap.dedent('''
        import tkinter as tk
        root = tk.Tk(); root.title("Калькулятор"); root.geometry("380x120")
        a = tk.Entry(root); b = tk.Entry(root); r = tk.Label(root, text="Результат:")
        def calc(event=None): r.config(text=f"Результат: {float(a.get()) + float(b.get())}")
        a.pack(); b.pack(); tk.Button(root, text="Вычислить", command=calc).pack(); r.pack()
        root.bind("<Return>", calc); a.focus_set(); root.mainloop()
    '''), encoding="utf-8")
    pyw = str(Path(sys.executable).with_name("pythonw.exe"))
    return keyboard_case("python_tkinter", f'"{pyw}" main.py', d, ["12", "key:tab", "5", "key:enter"], "Калькулятор")


@case("cpp_msvc")
def c16():
    d = WORK / "msvc"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.cpp").write_text('#include <iostream>\nint main(){ double a,b; std::cout << "Введите два числа: "; std::cin >> a >> b;'
                                ' std::cout << "Сумма: " << a + b << std::endl; return 0; }\n', encoding="utf-8")
    vs = REPORT["tools"]["vswhere"]["out"].strip().splitlines()[0]
    vcvars = Path(vs) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    code, out = run(f'call "{vcvars}" >nul && cl /nologo /utf-8 /EHsc /O2 main.cpp', cwd=d, timeout=600, shell=True)
    if code:
        raise RuntimeError(out)
    return console_case("cpp_msvc", '"main.exe"', d, "12 5\n", expect="Сумма: 17")


@case("php_site")
def c17():
    from forlabs.runner import winapi

    d = WORK / "phpsite"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.php").write_text('<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Библиотека</title></head><body>'
                                 '<h1>Книги</h1><ul><?php foreach (["Мастер и Маргарита","Война и мир"] as $b) echo "<li>$b</li>"; ?></ul>'
                                 '</body></html>', encoding="utf-8")
    srv = subprocess.Popen(["php", "-S", "127.0.0.1:8099", "-t", str(d)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prof = WORK / "edge-profile-php"
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    app = winapi.spawn(f'"{edge}" --user-data-dir="{prof}" --no-first-run --no-default-browser-check '
                       f'--disable-features=msEdgeFirstRunExperience --window-size=1100,700 --new-window http://127.0.0.1:8099/',
                       str(d), low=False, max_processes=64, memory_mb=4096)
    try:
        w = winapi.wait_window({app.pid}, 40, lambda x: "Библиотека" in x.title)
        if not w:
            raise RuntimeError("окно с сайтом не появилось")
        time.sleep(2)
        res = {"title": w.title, "shot": save(winapi.capture(w.hwnd), "php_site")}
        res["ok"] = not res["shot"]["blank"]
        return res
    finally:
        app.close()
        srv.terminate()


@case("hang_is_killed")
def c18():
    from forlabs.runner import winapi

    d = WORK / "hang"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text("while True:\n    pass\n", encoding="utf-8")
    app = winapi.spawn(f'"{sys.executable}" main.py', str(d), low=True, hidden=True)
    t = time.time()
    code = app.wait(2)
    app.kill()
    gone = app.wait(5)
    return {"ok": code is None and gone is not None, "killed_in": round(time.time() - t, 1)}


@case("crash_reported")
def c19():
    from forlabs.runner import winapi

    d = WORK / "crash"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    app = winapi.spawn(f'"{sys.executable}" main.py', str(d), low=True, hidden=True)
    code = app.wait(15)
    w = winapi.wait_window({app.pid}, 2)
    return {"ok": code == 3 and w is None, "exit": code}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    tools()
    for fn in (c1, c2, c3, c3b, c4, c5, c6, c7, c8, c9, c10, c11, c12, c13, c14, c15, c16, c17, c18, c19):
        fn()
    (OUT / "report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = [k for k, v in REPORT["cases"].items() if not v.get("ok")]
    print("FAILED:", bad)


if __name__ == "__main__":
    main()
