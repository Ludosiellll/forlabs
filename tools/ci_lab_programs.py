"""Windows (CI): программы лаб на всех языках — готовый план → проект → сборка инструментами сборщика →
запуск в песочнице → сценарий → снимок окна. Отчёт: build/probe/report.json, снимки: build/probe/shots/*.png."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forlabs.runner import languages, safety  # noqa: E402
from forlabs.runner.plan import ProjectPlan, validate  # noqa: E402
from forlabs.runner.shots import RunTarget, WindowsShooter  # noqa: E402

OUT = Path("build/probe")
SHOTS = OUT / "shots"
WORK = Path(os.environ.get("RUNNER_TEMP", "build")) / "lab-programs"

CS_CALC = '''using System;
using System.Windows.Forms;

namespace Calculator
{
    public class MainForm : Form
    {
        private TextBox txtA = new TextBox { Name = "txtA", Left = 20, Top = 20, Width = 120 };
        private TextBox txtB = new TextBox { Name = "txtB", Left = 160, Top = 20, Width = 120 };
        private ComboBox cmbOp = new ComboBox { Name = "cmbOp", Left = 20, Top = 60, Width = 260,
            DropDownStyle = ComboBoxStyle.DropDownList };
        private Button btnCalc = new Button { Name = "btnCalc", Text = "Вычислить", Left = 20, Top = 100, Width = 120 };
        private Label lblResult = new Label { Name = "lblResult", Text = "Результат:", Left = 160, Top = 105, Width = 200 };

        public MainForm()
        {
            Text = "Калькулятор";
            Width = 340;
            Height = 200;
            cmbOp.Items.AddRange(new object[] { "Сумма", "Разность", "Произведение" });
            cmbOp.SelectedIndex = 0;
            btnCalc.Click += BtnCalc_Click;
            Controls.AddRange(new Control[] { txtA, txtB, cmbOp, btnCalc, lblResult });
        }

        private void BtnCalc_Click(object sender, EventArgs e)
        {
            double a = double.Parse(txtA.Text), b = double.Parse(txtB.Text);
            double r = cmbOp.SelectedIndex == 0 ? a + b : cmbOp.SelectedIndex == 1 ? a - b : a * b;
            lblResult.Text = "Результат: " + r;
            MessageBox.Show("Ответ: " + r, "Результат");
        }

        [STAThread]
        static void Main()
        {
            Application.EnableVisualStyles();
            Application.Run(new MainForm());
        }
    }
}
'''

WPF_XAML = '''<Window x:Class="Books.MainWindow" xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" Title="Учёт книг" Width="380" Height="240">
    <StackPanel Margin="12">
        <TextBox x:Name="txtTitle" Margin="0,0,0,8"/>
        <Button x:Name="btnAdd" Content="Добавить" Click="BtnAdd_Click" Width="120" HorizontalAlignment="Left"/>
        <ListBox x:Name="lstBooks" Height="90" Margin="0,8,0,0"/>
    </StackPanel>
</Window>
'''
WPF_CS = '''using System.Windows;

namespace Books
{
    public partial class MainWindow : Window
    {
        public MainWindow()
        {
            InitializeComponent();
        }

        private void BtnAdd_Click(object sender, RoutedEventArgs e)
        {
            lstBooks.Items.Add(txtTitle.Text);
            txtTitle.Clear();
        }
    }
}
'''
WPF_APP_XAML = '''<Application x:Class="Books.App" xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
             xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" StartupUri="MainWindow.xaml"/>
'''
WPF_APP_CS = '''using System.Windows;

namespace Books
{
    public partial class App : Application
    {
    }
}
'''


def plan(language, kind, name, entry, files, shot):
    return {"language": language, "kind": kind, "name": name, "entry": entry,
            "files": [{"path": p, "content": c} for p, c in files.items()], "listings": [],
            "shots": [{"section": 1, "block": 1, **shot}]}


CASES = {
    "csharp_console": plan("csharp", "console", "Сумма", "Program.cs", {"Program.cs": '''using System;

class Program
{
    static void Main()
    {
        Console.Write("Введите первое число: ");
        double a = double.Parse(Console.ReadLine());
        Console.Write("Введите второе число: ");
        double b = double.Parse(Console.ReadLine());
        Console.WriteLine("Сумма: " + (a + b));
        Console.ReadKey();
    }
}
'''}, {"mode": "console", "input": "12\n5\n", "expect": "Сумма: 17"}),
    "csharp_winforms_messagebox": plan("csharp", "winforms", "Калькулятор", "MainForm.cs", {"MainForm.cs": CS_CALC},
                                       {"mode": "window", "expect": "Ответ: 17", "steps": [
                                           {"action": "set", "target": "txtA", "value": "12"},
                                           {"action": "set", "target": "txtB", "value": "5"},
                                           {"action": "select", "target": "cmbOp", "value": "Сумма"},
                                           {"action": "click", "target": "Вычислить"}]}),
    "csharp_wpf": plan("csharp", "wpf", "Книги", "App.xaml", {"App.xaml": WPF_APP_XAML, "App.xaml.cs": WPF_APP_CS,
                                                              "MainWindow.xaml": WPF_XAML, "MainWindow.xaml.cs": WPF_CS},
                       {"mode": "window", "expect": "Война и мир", "steps": [
                           {"action": "set", "target": "txtTitle", "value": "Мастер и Маргарита"},
                           {"action": "click", "target": "btnAdd"},
                           {"action": "set", "target": "txtTitle", "value": "Война и мир"},
                           {"action": "click", "target": "btnAdd"}]}),
    "cpp_console": plan("cpp", "console", "Массив", "main.cpp", {"main.cpp": '''#include <iostream>
using namespace std;

int main()
{
    int n;
    cout << "Введите количество элементов: ";
    cin >> n;
    int sum = 0;
    for (int i = 0; i < n; i++)
    {
        int x;
        cin >> x;
        sum += x;
    }
    cout << "Сумма элементов: " << sum << endl;
    return 0;
}
'''}, {"mode": "console", "input": "3\n4 5 8\n", "expect": "Сумма элементов: 17"}),
    "python_console": plan("python", "console", "Список", "main.py", {"main.py": '''numbers = [int(x) for x in input("Введите числа через пробел: ").split()]
print("Максимум:", max(numbers))
print("Сумма:", sum(numbers))
'''}, {"mode": "console", "input": "12 5\n", "expect": "Сумма: 17"}),
    "python_tkinter": plan("python", "gui", "Калькулятор", "main.py", {"main.py": '''import tkinter as tk


def calc(event=None):
    result.config(text=f"Результат: {float(entry_a.get()) + float(entry_b.get())}")


root = tk.Tk()
root.title("Калькулятор")
root.geometry("380x140")
entry_a = tk.Entry(root)
entry_b = tk.Entry(root)
entry_a.pack(pady=4)
entry_b.pack(pady=4)
tk.Button(root, text="Вычислить", command=calc).pack()
result = tk.Label(root, text="Результат:")
result.pack()
root.bind("<Return>", calc)
entry_a.focus_force()
root.mainloop()
'''}, {"mode": "window", "steps": [{"action": "type", "value": "12"}, {"action": "key", "value": "tab"},
                                    {"action": "type", "value": "5"}, {"action": "key", "value": "enter"}]}),
    "java_console": plan("java", "console", "Студенты", "Main.java", {"Main.java": '''import java.util.Scanner;

public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        System.out.print("Введите два числа: ");
        int a = in.nextInt();
        int b = in.nextInt();
        System.out.println("Сумма: " + (a + b));
    }
}
'''}, {"mode": "console", "input": "12 5\n", "expect": "Сумма: 17"}),
    "java_swing": plan("java", "gui", "Калькулятор", "Main.java", {"Main.java": '''import javax.swing.*;
import java.awt.*;

public class Main {
    public static void main(String[] args) {
        SwingUtilities.invokeLater(() -> {
            JFrame frame = new JFrame("Калькулятор");
            frame.setLayout(new FlowLayout());
            JTextField a = new JTextField(6);
            JTextField b = new JTextField(6);
            JButton calc = new JButton("Вычислить");
            JLabel result = new JLabel("Результат:");
            calc.addActionListener(e -> result.setText("Результат: "
                    + (Integer.parseInt(a.getText()) + Integer.parseInt(b.getText()))));
            frame.add(a);
            frame.add(b);
            frame.add(calc);
            frame.add(result);
            frame.getRootPane().setDefaultButton(calc);
            frame.setSize(420, 120);
            frame.setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE);
            frame.setVisible(true);
            a.requestFocusInWindow();
        });
    }
}
'''}, {"mode": "window", "steps": [{"action": "type", "value": "12"}, {"action": "key", "value": "tab"},
                                    {"action": "type", "value": "5"}, {"action": "key", "value": "enter"}]}),
    "node_console": plan("javascript", "console", "Сумма", "main.js", {"main.js": '''const readline = require("readline");
const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

rl.question("Введите два числа: ", (line) => {
  const [a, b] = line.split(" ").map(Number);
  console.log("Сумма:", a + b);
  rl.close();
});
'''}, {"mode": "console", "input": "12 5\n", "expect": "Сумма: 17"}),
    "web_page": plan("web", "web", "Кофейня", "index.html", {"index.html": '''<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>Кофейня «Зерно»</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <h1>Кофейня «Зерно»</h1>
  <ul class="menu"><li>Капучино — 5 р.</li><li>Латте — 6 р.</li></ul>
</body>
</html>
''', "style.css": "body { font-family: Segoe UI, sans-serif; background: #f4efe6; margin: 40px; }\nh1 { color: #6b3e26; }\n"},
                     {"mode": "browser", "url": "index.html"}),
    "php_console": plan("php", "console", "Сумма", "main.php", {"main.php": '''<?php
echo "Введите два числа: ";
[$a, $b] = array_map("intval", explode(" ", trim(fgets(STDIN))));
echo "Сумма: " . ($a + $b) . PHP_EOL;
'''}, {"mode": "console", "input": "12 5\n", "expect": "Сумма: 17"}),
    "php_web": plan("php", "web", "Библиотека", "index.php", {"index.php": '''<!DOCTYPE html>
<html lang="ru">
<head><meta charset="UTF-8"><title>Библиотека</title></head>
<body>
<h1>Книги</h1>
<ul>
<?php foreach (["Мастер и Маргарита", "Война и мир"] as $book): ?>
  <li><?= htmlspecialchars($book) ?></li>
<?php endforeach; ?>
</ul>
</body>
</html>
'''}, {"mode": "browser", "url": "index.php"}),
    "php_form_post": plan("php", "web", "Анкета", "index.php", {"index.php": '''<!DOCTYPE html>
<html lang="ru">
<head><meta charset="UTF-8"><title>Анкета</title></head>
<body>
<h1>Анкета студента</h1>
<?php if ($_SERVER["REQUEST_METHOD"] === "POST"): ?>
  <p>Здравствуйте, <?= htmlspecialchars($_POST["name"] ?? "") ?>! Ваша группа: <?= htmlspecialchars($_POST["group"] ?? "") ?></p>
<?php else: ?>
  <form method="post">
    <label>Имя <input id="name" name="name"></label>
    <label>Группа <input id="group" name="group"></label>
    <button type="submit">Отправить</button>
  </form>
<?php endif; ?>
</body>
</html>
'''}, {"mode": "browser", "url": "index.php", "expect": "Здравствуйте, Иван",
       "steps": [{"action": "set", "target": "id:name", "value": "Иван"},
                 {"action": "set", "target": "id:group", "value": "РС02-23"},
                 {"action": "click", "target": "Отправить"}]}),
    "go_console": plan("go", "console", "Сумма", "main.go", {"main.go": '''package main

import "fmt"

func main() {
	var a, b int
	fmt.Print("Введите два числа: ")
	fmt.Scan(&a, &b)
	fmt.Println("Сумма:", a+b)
}
'''}, {"mode": "console", "input": "12 5\n", "expect": "Сумма: 17"}),
    "mysql_script": plan("mysql", "sql", "Библиотека", "library.sql", {"library.sql": '''CREATE DATABASE library;
USE library;
CREATE TABLE books (id INT PRIMARY KEY AUTO_INCREMENT, title VARCHAR(100), author VARCHAR(100));
'''}, {"mode": "console", "expect": "Булгаков",
       "input": "CREATE DATABASE library;\nUSE library;\nCREATE TABLE books (id INT PRIMARY KEY AUTO_INCREMENT, "
                "title VARCHAR(100), author VARCHAR(100));\nINSERT INTO books (title, author) VALUES "
                "('Мастер и Маргарита', 'Булгаков'), ('Война и мир', 'Толстой');\nSELECT * FROM books;\n"}),
    "mysql_users": plan("mysql", "sql", "Права", "users.sql", {"users.sql": '''DROP USER IF EXISTS 'student'@'localhost';
CREATE USER 'student'@'localhost' IDENTIFIED BY 'Pass_2026';
DROP DATABASE IF EXISTS shop;
CREATE DATABASE shop;
GRANT SELECT, INSERT ON shop.* TO 'student'@'localhost';
SELECT user, host FROM mysql.user WHERE user = 'student';
'''}, {"mode": "console", "input": "source users.sql\nSHOW GRANTS FOR 'student'@'localhost';\n",
         "expect": "GRANT SELECT, INSERT ON"}),
}


# песочница: программа нарочно пробует прочитать файл владельца и выйти в интернет
PROBE = '''import os, urllib.request
try:
    open(os.path.join(os.environ.get("FORLABS_SECRET", "x"))).read()
    print("ФАЙЛ: прочитан")
except OSError:
    print("ФАЙЛ: закрыт")
try:
    urllib.request.urlopen("https://example.com", timeout=10).read(100)
    print("СЕТЬ: есть")
except Exception:
    print("СЕТЬ: закрыта")
try:
    open("своя запись.txt", "w", encoding="utf-8").write("ok")
    print("ЗАПИСЬ: да")
except OSError:
    print("ЗАПИСЬ: нет")
'''
SANDBOX_CASES = {
    "sandbox_no_internet": (plan("python", "console", "Проверка", "main.py", {"main.py": PROBE},
                                 {"mode": "console", "expect": "ФАЙЛ: закрыт"}), False, "СЕТЬ: закрыта"),
    "sandbox_with_internet": (plan("python", "console", "Проверка", "main.py", {"main.py": PROBE},
                                   {"mode": "console", "expect": "ФАЙЛ: закрыт"}), True, "СЕТЬ: есть"),
}


def mysql_variants(tools, report) -> None:
    """Диагностика: как запуск клиента MySQL доходит до программы (напрямую, через файл настроек, по имени)."""
    from forlabs.pipeline.project import start_mysql
    from forlabs.runner import mysqlrun
    from forlabs.runner.shots import run_dir

    out = {}
    report["mysql_variants"] = out
    try:  # для сравнения: тот же сервер с обычными правами
        s2 = mysqlrun.start(tools["mysql"].path, run_dir(), low=False)
        out["medium_il"] = "ok"
        s2.stop()
    except Exception as e:  # noqa: BLE001
        out["medium_il"] = f"{e}: {getattr(e, 'detail', '')}"
    srv = start_mysql(tools["mysql"].path)  # как в ForLabs: папка запуска с меткой Low, сервер с пониженными правами
    try:
        base = srv.client()
        r = subprocess.run([*base, "-e", "SELECT 'прямой запуск'"], capture_output=True, text=True, timeout=30)
        out["direct"] = [r.returncode, (r.stdout + r.stderr)[:300]]
        cnf = WORK / "client.cnf"
        cnf.write_text(f"[client]\nuser=root\nhost=127.0.0.1\nport={srv.port}\ndefault-character-set=utf8mb4\n",
                       encoding="utf-8")
        r = subprocess.run([base[0], f"--defaults-extra-file={cnf}", "-e", "SELECT 'через файл'"], capture_output=True,
                           text=True, timeout=30)
        out["cnf"] = [r.returncode, (r.stdout + r.stderr)[:300]]
        line = " ".join(f'"{a}"' if " " in a else a for a in base)
        r = subprocess.run(f'cmd.exe /d /s /c "chcp 65001>nul & {line} -e \"SELECT 1\""', capture_output=True,
                           text=True, timeout=30, shell=False)
        out["cmd_wrap"] = [r.returncode, (r.stdout + r.stderr)[:300]]
    finally:
        srv.stop()
    report["mysql_variants"] = out
    print("mysql варианты:", out, flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    tools = languages.detect()
    report: dict = {"tools": {k: [t.path, t.version] for k, t in tools.items()}, "cases": {}}
    print("инструменты:", {k: t.version for k, t in tools.items()}, flush=True)
    srv = None  # MySQL поднимается на каждую лабу отдельно (mysqlrun)
    shooter = WindowsShooter()
    try:
        mysql_variants(tools, report)
    except Exception as e:  # noqa: BLE001
        report.setdefault("mysql_variants", {}).update(error=f"{type(e).__name__}: {e}", detail=getattr(e, "detail", ""))
    secret = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents" / "forlabs-secret.txt"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("личное", encoding="utf-8")
    runs = [(name, raw, tools, {}) for name, raw in CASES.items()]
    runs += [(name, raw, tools, {"FORLABS_SECRET": str(secret)}) for name, (raw, _, _) in SANDBOX_CASES.items()]
    # профиль студента с кириллицей и пробелом (C:\\Users\\Иван Петров): папки запуска и сервер MySQL — под ним
    runs.append(("mysql_cyrillic_profile", CASES["mysql_script"], tools,
                 {"USERPROFILE": str(WORK / "Профиль Иван Петров")}))
    maria = Path(os.environ.get("FORLABS_MARIADB", "-")) / "bin" / "mysql.exe"
    if maria.exists():  # MariaDB из XAMPP: свой mysql_install_db.exe, свой клиент
        runs.append(("mysql_mariadb_xampp", CASES["mysql_script"],
                     {**tools, "mysql": languages.Toolchain("mysql", str(maria))}, {}))
    try:
        for name, raw, tools, env in runs:
            t0 = time.time()
            res: dict = {}
            saved = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            try:
                p = ProjectPlan.model_validate(raw)
                problems = validate(p)
                if problems:
                    raise RuntimeError("план: " + "; ".join(problems))
                if name not in SANDBOX_CASES and (blocked := safety.scan(p)):  # как в ForLabs: опасное не запускается
                    raise RuntimeError("проверка безопасности: " + "; ".join(blocked))
                lack = languages.missing(p, tools)
                if lack:
                    raise RuntimeError("нет инструментов: " + ", ".join(lack))
                work = WORK / name
                shutil.rmtree(work, ignore_errors=True)
                languages.materialize(p, work, tools)
                b = languages.build(work, p, tools)
                res["build"] = b.ok
                if not b.ok:
                    raise RuntimeError("сборка: " + b.log[-1500:])
                spec = languages.run_spec(p, b.artifact, work, tools)
                if p.language == "mysql":  # свой сервер с чистой папкой данных — как в ForLabs у студента
                    from forlabs.pipeline.project import start_mysql

                    lab_srv = start_mysql(tools["mysql"].path)
                    spec.cmd = lab_srv.client()
                    res["mysql_port"] = lab_srv.port
                php_web = bool(spec.url) and p.language == "php"
                target = RunTarget(cmd=[] if php_web else list(spec.cmd), cwd=Path(spec.cwd), console=spec.console,
                                   url=spec.url or None, env=dict(spec.env or {}),
                                   server=list(spec.cmd) if php_web else None, title=None,
                                   network=name in SANDBOX_CASES and SANDBOX_CASES[name][1])
                if name in SANDBOX_CASES:
                    target.env = {**target.env, "FORLABS_SECRET": env["FORLABS_SECRET"]}
                shot = p.shots[0]
                try:
                    r = shooter.shoot(target, shot, SHOTS / f"{name}.png")
                finally:
                    if p.language == "mysql":
                        lab_srv.stop()
                res.update(ok=r.ok, reason=r.reason, seen=r.seen[:1500], cmd=list(target.cmd), sandbox=r.sandbox)
                if name in SANDBOX_CASES:
                    must = SANDBOX_CASES[name][2]
                    res["ok"] = r.ok and r.sandbox == "container" and must in r.seen and "ЗАПИСЬ: да" in r.seen
                    if not res["ok"]:
                        res["reason"] = (r.reason or "") + f" | нужно: песочница, «{must}», «ЗАПИСЬ: да»"
            except Exception as e:  # noqa: BLE001
                res.update(ok=False, error=f"{type(e).__name__}: {e}", detail=getattr(e, "detail", ""),
                           trace=traceback.format_exc()[-1500:])
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            res["sec"] = round(time.time() - t0, 1)
            report["cases"][name] = res
            print(f"[{name}] {'OK' if res.get('ok') else 'FAIL'} {res.get('reason') or res.get('error') or ''}",
                  flush=True)
    finally:
        if srv is not None:
            srv.terminate()
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = [k for k, v in report["cases"].items() if not v.get("ok")]
    print("НЕ ПРОШЛИ:", bad, flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
