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
          try { System.IO.File.WriteAllText(System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "forlabs-probe.txt"), "x"); Text += " [docs-write-ok]"; }
          catch (Exception) { Text += " [docs-write-denied]"; }
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
    import clr  # pythonnet

    clr.AddReference("UIAutomationClient")
    clr.AddReference("UIAutomationTypes")
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

    exe = build_cs("ProbeForms", FORM_CS, CSPROJ_WIN)
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


def type_into_console(pid, text):
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


def console_case(name, cmd, cwd, inputs, low=False, expect=None, conhost=True):
    from forlabs.runner import winapi

    full = f'conhost.exe {cmd}' if conhost else cmd
    app = winapi.spawn(full, str(cwd), low=low, new_console=not conhost)
    try:
        w = winapi.wait_window({app.pid}, 30, lambda x: x.cls in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"))
        if not w:
            raise RuntimeError(f"окно консоли не появилось (жив={app.alive()})")
        time.sleep(1.5)
        tree = winapi.process_tree({app.pid})
        target = next((p for p in tree if p != app.pid), app.pid)
        type_into_console(target if conhost else app.pid, inputs)
        time.sleep(1.5)
        res = {"title": w.title, "class": w.cls}
        res["shot"] = save(winapi.capture(w.hwnd), name)
        res["ok"] = not res["shot"]["blank"]
        return res
    finally:
        app.close()


@case("console_cs")
def c3():
    exe = build_cs("ProbeConsole", CONSOLE_CS, CSPROJ_CON)
    return console_case("console_cs", f'"{exe}"', exe.parent, "12\n5\n")


@case("console_cs_low")
def c3b():
    exe = build_cs("ProbeConsole2", CONSOLE_CS, CSPROJ_CON)
    from forlabs.runner import winapi
    winapi.low_label_dir(str(exe.parent))
    return console_case("console_cs_low", f'"{exe}"', exe.parent, "12\n5\n", low=True)


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
    code, out = run(["powershell", "-NoProfile", "-Command",
                     "Get-Service *mysql* | Select-Object -First 1 | Start-Service; Get-Service *mysql* | Format-List Name,Status"],
                    timeout=120)
    res = {"start": out[-500:]}
    code, out = run(["mysql", "-u", "root", "-proot", "-e", "SELECT VERSION(); SHOW DATABASES;"], timeout=60)
    res["root_root"] = [code, out[-500:]]
    if code:
        code, out = run(["mysql", "-u", "root", "-e", "SELECT VERSION();"], timeout=60)
        res["root_nopass"] = [code, out[-500:]]
    res["ok"] = code == 0
    return res


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    tools()
    for fn in (c1, c2, c3, c3b, c4, c5, c6, c7, c8, c9, c10, c11):
        fn()
    (OUT / "report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = [k for k, v in REPORT["cases"].items() if not v.get("ok")]
    print("FAILED:", bad)


if __name__ == "__main__":
    main()
