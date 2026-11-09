"""Windows (CI): окна программ, как в лабах студентов, — выпадающий список без выбранного пункта, вторая форма по
кнопке, программа с ошибкой (список без пунктов) и её починка по кругу «снимок не вышел → модель исправила → снято».
Отчёт: build/probe/windows.json, снимки: build/probe/shots/win_*.png."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "pipeline"))

from forlabs.runner import languages  # noqa: E402
from forlabs.runner.plan import ProjectPlan, validate  # noqa: E402
from forlabs.runner.shots import RunTarget, WindowsShooter  # noqa: E402

OUT = Path("build/probe").resolve()
SHOTS = OUT / "shots"
WORK = Path(os.environ.get("RUNNER_TEMP", "build")).resolve() / "window-probe"

PROGRAM = '''using System;
using System.Windows.Forms;

namespace TestLibrary
{
    internal static class Program
    {
        [STAThread]
        static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new Form1());
        }
    }
}
'''

CALCULATOR = '''using System;

namespace ClassLibrary1
{
    public class Calculator
    {
        public static double Summ(int a, int b)
        {
            return a + b;
        }

        public static double Multiply(int a, int b)
        {
            return a * b;
        }
    }

    public class Condenser
    {
        public static double Calculate(double eps, double s, double d)
        {
            return (eps * s) / (4.0 * Math.PI * d);
        }
    }
}
'''

CLEAR = '''        private void btnClear_Click(object sender, EventArgs e)
        {
            txtFirst.Text = "";
            txtSecond.Text = "";
            cmbOperation.SelectedIndex = -1;
            txtResult.Text = "";
        }'''

FORM1 = '''using System;
using System.Windows.Forms;
using ClassLibrary1;

namespace TestLibrary
{
    public partial class Form1 : Form
    {
        public Form1()
        {
            InitializeComponent();
        }

        private void btnCalculate_Click(object sender, EventArgs e)
        {
            int first = 0;
            int second = 0;

            if (!int.TryParse(txtFirst.Text, out first) || !int.TryParse(txtSecond.Text, out second))
            {
                MessageBox.Show("Введите целые числа!", "Ошибка");
                return;
            }

            string operation = cmbOperation.SelectedItem.ToString();
            double result = 0;

            if (operation == "Сложение")
            {
                result = Calculator.Summ(first, second);
            }
            else if (operation == "Умножение")
            {
                result = Calculator.Multiply(first, second);
            }

            txtResult.Text = result.ToString("F6");
        }

''' + CLEAR + '''

        private void btnToCondenser_Click(object sender, EventArgs e)
        {
            Form2 form2 = new Form2();
            form2.Show();
        }
    }
}
'''

ITEMS = '''            this.cmbOperation.Items.AddRange(new object[] { "Сложение", "Умножение" });
'''

FORM1_DESIGNER = '''namespace TestLibrary
{
    partial class Form1
    {
        private System.ComponentModel.IContainer components = null;
        private System.Windows.Forms.Label lblFirst;
        private System.Windows.Forms.Label lblSecond;
        private System.Windows.Forms.Label lblOperation;
        private System.Windows.Forms.Label lblResult;
        private System.Windows.Forms.TextBox txtFirst;
        private System.Windows.Forms.TextBox txtSecond;
        private System.Windows.Forms.ComboBox cmbOperation;
        private System.Windows.Forms.TextBox txtResult;
        private System.Windows.Forms.Button btnCalculate;
        private System.Windows.Forms.Button btnClear;
        private System.Windows.Forms.Button btnToCondenser;

        protected override void Dispose(bool disposing)
        {
            if (disposing && (components != null))
            {
                components.Dispose();
            }
            base.Dispose(disposing);
        }

        private void InitializeComponent()
        {
            this.lblFirst = new System.Windows.Forms.Label();
            this.lblSecond = new System.Windows.Forms.Label();
            this.lblOperation = new System.Windows.Forms.Label();
            this.lblResult = new System.Windows.Forms.Label();
            this.txtFirst = new System.Windows.Forms.TextBox();
            this.txtSecond = new System.Windows.Forms.TextBox();
            this.cmbOperation = new System.Windows.Forms.ComboBox();
            this.txtResult = new System.Windows.Forms.TextBox();
            this.btnCalculate = new System.Windows.Forms.Button();
            this.btnClear = new System.Windows.Forms.Button();
            this.btnToCondenser = new System.Windows.Forms.Button();
            this.SuspendLayout();
            this.lblFirst.AutoSize = true;
            this.lblFirst.Location = new System.Drawing.Point(20, 25);
            this.lblFirst.Name = "lblFirst";
            this.lblFirst.Text = "Первый операнд:";
            this.txtFirst.Location = new System.Drawing.Point(160, 22);
            this.txtFirst.Name = "txtFirst";
            this.txtFirst.Size = new System.Drawing.Size(120, 20);
            this.lblSecond.AutoSize = true;
            this.lblSecond.Location = new System.Drawing.Point(20, 60);
            this.lblSecond.Name = "lblSecond";
            this.lblSecond.Text = "Второй операнд:";
            this.txtSecond.Location = new System.Drawing.Point(160, 57);
            this.txtSecond.Name = "txtSecond";
            this.txtSecond.Size = new System.Drawing.Size(120, 20);
            this.lblOperation.AutoSize = true;
            this.lblOperation.Location = new System.Drawing.Point(20, 95);
            this.lblOperation.Name = "lblOperation";
            this.lblOperation.Text = "Операция:";
            this.cmbOperation.DropDownStyle = System.Windows.Forms.ComboBoxStyle.DropDownList;
//ITEMS//            this.cmbOperation.Location = new System.Drawing.Point(160, 92);
            this.cmbOperation.Name = "cmbOperation";
            this.cmbOperation.Size = new System.Drawing.Size(120, 21);
            this.lblResult.AutoSize = true;
            this.lblResult.Location = new System.Drawing.Point(20, 130);
            this.lblResult.Name = "lblResult";
            this.lblResult.Text = "Результат:";
            this.txtResult.Location = new System.Drawing.Point(160, 127);
            this.txtResult.Name = "txtResult";
            this.txtResult.ReadOnly = true;
            this.txtResult.Size = new System.Drawing.Size(120, 20);
            this.btnCalculate.Location = new System.Drawing.Point(20, 170);
            this.btnCalculate.Name = "btnCalculate";
            this.btnCalculate.Size = new System.Drawing.Size(110, 30);
            this.btnCalculate.Text = "Вычислить";
            this.btnCalculate.UseVisualStyleBackColor = true;
            this.btnCalculate.Click += new System.EventHandler(this.btnCalculate_Click);
            this.btnClear.Location = new System.Drawing.Point(150, 170);
            this.btnClear.Name = "btnClear";
            this.btnClear.Size = new System.Drawing.Size(110, 30);
            this.btnClear.Text = "Очистить";
            this.btnClear.UseVisualStyleBackColor = true;
            this.btnClear.Click += new System.EventHandler(this.btnClear_Click);
            this.btnToCondenser.Location = new System.Drawing.Point(20, 210);
            this.btnToCondenser.Name = "btnToCondenser";
            this.btnToCondenser.Size = new System.Drawing.Size(240, 30);
            this.btnToCondenser.Text = "Расчет емкости конденсатора";
            this.btnToCondenser.UseVisualStyleBackColor = true;
            this.btnToCondenser.Click += new System.EventHandler(this.btnToCondenser_Click);
            this.ClientSize = new System.Drawing.Size(320, 260);
            this.Controls.Add(this.lblFirst);
            this.Controls.Add(this.txtFirst);
            this.Controls.Add(this.lblSecond);
            this.Controls.Add(this.txtSecond);
            this.Controls.Add(this.lblOperation);
            this.Controls.Add(this.cmbOperation);
            this.Controls.Add(this.lblResult);
            this.Controls.Add(this.txtResult);
            this.Controls.Add(this.btnCalculate);
            this.Controls.Add(this.btnClear);
            this.Controls.Add(this.btnToCondenser);
            this.Name = "Form1";
            this.Text = "Калькулятор";
            this.ResumeLayout(false);
            this.PerformLayout();
        }
    }
}
'''
DESIGNER_GOOD = FORM1_DESIGNER.replace("//ITEMS//", ITEMS)
DESIGNER_EMPTY = FORM1_DESIGNER.replace("//ITEMS//", "")  # как у студента: у списка нет ни одного пункта

FORM2 = '''using System;
using System.Windows.Forms;
using ClassLibrary1;

namespace TestLibrary
{
    public class Form2 : Form
    {
        private TextBox txtS = new TextBox { Name = "txtS", Left = 160, Top = 22, Width = 120 };
        private TextBox txtD = new TextBox { Name = "txtD", Left = 160, Top = 57, Width = 120 };
        private TextBox txtEps = new TextBox { Name = "txtEps", Left = 160, Top = 92, Width = 120 };
        private TextBox txtResult = new TextBox { Name = "txtResult", Left = 160, Top = 127, Width = 120, ReadOnly = true };
        private Button btnCalculate = new Button { Name = "btnCalculate", Left = 20, Top = 170, Width = 110, Height = 30,
            Text = "Вычислить" };

        public Form2()
        {
            Text = "Расчет емкости конденсатора";
            ClientSize = new System.Drawing.Size(310, 220);
            Controls.Add(new Label { Name = "lblS", Left = 20, Top = 25, AutoSize = true, Text = "Площадь S:" });
            Controls.Add(new Label { Name = "lblD", Left = 20, Top = 60, AutoSize = true, Text = "Расстояние d:" });
            Controls.Add(new Label { Name = "lblEps", Left = 20, Top = 95, AutoSize = true, Text = "Проницаемость eps:" });
            Controls.Add(new Label { Name = "lblResult", Left = 20, Top = 130, AutoSize = true, Text = "Емкость C:" });
            Controls.AddRange(new Control[] { txtS, txtD, txtEps, txtResult, btnCalculate });
            btnCalculate.Click += btnCalculate_Click;
        }

        private void btnCalculate_Click(object sender, EventArgs e)
        {
            double s, d, eps;
            if (!double.TryParse(txtS.Text, out s) || !double.TryParse(txtD.Text, out d)
                || !double.TryParse(txtEps.Text, out eps))
            {
                MessageBox.Show("Введите числа!", "Ошибка");
                return;
            }
            txtResult.Text = Condenser.Calculate(eps, s, d).ToString("F6");
        }
    }
}
'''

SUM_STEPS = [{"action": "set", "target": "txtFirst", "value": "12"},
             {"action": "set", "target": "txtSecond", "value": "8"},
             {"action": "select", "target": "cmbOperation", "value": "Сложение"},
             {"action": "click", "target": "btnCalculate"}]
FORM2_STEPS = [{"action": "set", "target": "txtS", "value": "10"}, {"action": "set", "target": "txtD", "value": "2"},
               {"action": "set", "target": "txtEps", "value": "1"}, {"action": "click", "target": "btnCalculate"}]
OPEN_FORM2 = {"action": "click", "target": "btnToCondenser"}


def files(designer: str) -> list[dict]:
    return [{"path": p, "content": c} for p, c in {
        "Program.cs": PROGRAM, "Calculator.cs": CALCULATOR, "Form1.cs": FORM1, "Form1.Designer.cs": designer,
        "Form2.cs": FORM2}.items()]


def plan(designer: str, shots: list[dict]) -> dict:
    return {"language": "csharp", "kind": "winforms", "name": "Калькулятор", "entry": "Program.cs",
            "files": files(designer), "listings": [{"section": 1, "block": 2, "file": "Form1.cs"}], "shots": shots}


def build(name: str, raw: dict, tools):
    p = ProjectPlan.model_validate(raw)
    problems = validate(p)
    if problems:
        raise RuntimeError("план: " + "; ".join(problems))
    work = WORK / name
    shutil.rmtree(work, ignore_errors=True)
    languages.materialize(p, work, tools)
    b = languages.build(work, p, tools)
    if not b.ok:
        raise RuntimeError("сборка: " + b.log[-1500:])
    spec = languages.run_spec(p, b.artifact, work, tools)
    return RunTarget(cmd=list(spec.cmd), cwd=Path(spec.cwd), console=spec.console, env=dict(spec.env or {}))


def direct_cases(tools, report: dict) -> None:
    """Сценарии напрямую через WindowsShooter: что он снимает и что сообщает, когда снять нельзя."""
    shooter = WindowsShooter()
    good = build("good", plan(DESIGNER_GOOD, []), tools)
    empty = build("empty", plan(DESIGNER_EMPTY, []), tools)
    cases = {
        # список без выбранного пункта: выбрать «Сложение», посчитать
        "list_without_selection": (good, {"mode": "window", "steps": SUM_STEPS, "expect": "20"},
                                   lambda r: r.ok and "20" in r.seen),
        # пункт записан строчными буквами — всё равно выбирается
        "item_in_other_case": (good, {"mode": "window", "expect": "20", "steps": [
            *SUM_STEPS[:2], {"action": "select", "target": "cmbOperation", "value": "сложение"}, SUM_STEPS[3]]},
            lambda r: r.ok),
        # вторая форма открывается кнопкой: шаги идут в новом окне, снимается оно
        "second_form_by_button": (good, {"mode": "window", "steps": [OPEN_FORM2, *FORM2_STEPS], "expect": "397887"},
                                  lambda r: r.ok and "Площадь" in r.seen and "397887" in r.seen),
        # программа с ошибкой: список пуст — причина названа, окно описано, второго запуска нет
        "empty_list_is_explained": (empty, {"mode": "window", "steps": SUM_STEPS, "expect": "20"},
                                    lambda r: (not r.ok and r.kind == "ui" and "список пуст" in r.reason
                                               and "cmbOperation" in r.ui and "пунктов нет" in r.ui
                                               and "btnToCondenser" in r.ui and r.sandbox == "container")),
        # сценарий забыл открыть вторую форму: причина и то, что в окне есть на самом деле
        "second_form_not_opened": (good, {"mode": "window", "steps": FORM2_STEPS, "expect": "397887"},
                                   lambda r: (not r.ok and r.kind == "ui" and "txtS" in r.reason
                                              and "Расчет емкости конденсатора" in r.ui)),
        # нет такого пункта: в причине — какие пункты есть
        "wrong_item_lists_real_ones": (good, {"mode": "window", "expect": "20", "steps": [
            *SUM_STEPS[:2], {"action": "select", "target": "cmbOperation", "value": "Деление"}, SUM_STEPS[3]]},
            lambda r: not r.ok and r.kind == "ui" and "Сложение, Умножение" in r.reason),
    }
    for name, (target, shot, check) in cases.items():
        t0 = time.time()
        res: dict = {}
        try:
            r = shooter.shoot(target, shot, SHOTS / f"win_{name}.png")
            res.update(shot_ok=r.ok, reason=r.reason, kind=r.kind, sandbox=r.sandbox, seen=r.seen[:800], ui=r.ui[:2500],
                       ok=bool(check(r)))
        except Exception as e:  # noqa: BLE001
            res.update(ok=False, error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-1500:])
        res["sec"] = round(time.time() - t0, 1)
        report["cases"][name] = res
        print(f"[{name}] {'OK' if res.get('ok') else 'FAIL'} {res.get('reason') or res.get('error') or ''}", flush=True)


def repair_case(report: dict) -> None:
    """Весь этап «Проект» с записанными ответами модели: программа со списком без пунктов и сценарий, который не
    открывает вторую форму, → оба снимка не вышли → «модель» вернула исправление → программа пересобрана, снято."""
    from test_run import ANS, FIXTURE, RAW, setup

    name, res, t0 = "repair_round_fixes_program_and_scenario", {}, time.time()
    home = Path(tempfile.mkdtemp(prefix="forlabs-probe-"))
    os.environ["FORLABS_HOME"] = str(home / "home")
    os.environ.pop("FORLABS_API_KEY", None)
    try:
        sec1 = {"blocks": [
            {"type": "paragraph", "text": "Программа складывает два числа: для 12 и 8 получается 20."},
            {"type": "listing", "topic": "Исходный код очистки полей формы", "language": "csharp", "code": CLEAR},
            {"type": "figure", "kind": "screenshot", "topic": "Окно калькулятора с результатом сложения двух чисел",
             "instruction": "Запусти программу, введи 12 и 8, выбери «Сложение» и нажми «Вычислить»"}]}
        sec2 = {"blocks": [
            {"type": "paragraph", "text": "Вторая форма считает емкость конденсатора."},
            {"type": "figure", "kind": "screenshot", "topic": "Окно формы с расчетом емкости конденсатора",
             "instruction": "Открой вторую форму, введи S = 10, d = 2, eps = 1 и нажми «Вычислить»"}]}
        broken = plan(DESIGNER_EMPTY, [
            {"section": 1, "block": 3, "mode": "window", "steps": SUM_STEPS, "expect": "20"},
            {"section": 2, "block": 2, "mode": "window", "steps": FORM2_STEPS, "expect": "Емкость"}])
        fix = {"files": [{"path": "Form1.Designer.cs", "content": DESIGNER_GOOD}],
               "shots": [{"section": 2, "block": 2, "mode": "window", "steps": [OPEN_FORM2, *FORM2_STEPS],
                          "expect": "Емкость"}]}
        pipe, fake = setup(home / "home", home, [{**RAW, "main_tasks": ["Калькулятор", "Конденсатор"]}, sec1, sec2,
                                                 ANS, broken, fix])
        pipe.projects = True
        pipe.shooter = None  # настоящий WindowsShooter
        st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
        st = pipe.confirm(pipe.parse(st, date="24.09.2026"), None)
        st = pipe.execute(st)
        figs = [b for s in st.report.sections for b in s.blocks if b.type == "figure"]
        asked = "\n".join(m["content"] for m in fake.calls[5]) if len(fake.calls) > 5 else ""
        hand_in = list((home / "сдача").rglob("Form1.Designer.cs"))
        for i, f in enumerate(figs, start=1):
            if f.image_path:
                shutil.copy(pipe.lab_dir(st) / f.image_path, SHOTS / f"win_repair_{i}.png")
        res.update(
            built=bool(st.project and st.project.built), shot_fixes=st.project.shot_fixes if st.project else None,
            figures=[{"topic": f.topic, "image": bool(f.image_path), "note": f.auto_note} for f in figs],
            model_saw_empty_list="список пуст" in asked and "пунктов нет" in asked,
            model_saw_button_of_second_form="btnToCondenser" in asked and "txtS" in asked,
            hand_in_fixed=bool(hand_in) and "Items.AddRange" in hand_in[0].read_text(encoding="utf-8"),
            llm_calls=len(fake.calls), asked=asked[:3000])
        res["ok"] = bool(res["built"] and res["shot_fixes"] == 1 and all(f["image"] for f in res["figures"])
                         and res["model_saw_empty_list"] and res["model_saw_button_of_second_form"]
                         and res["hand_in_fixed"])
    except Exception as e:  # noqa: BLE001
        res.update(ok=False, error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-2500:])
    finally:
        shutil.rmtree(home, ignore_errors=True)
    res["sec"] = round(time.time() - t0, 1)
    report["cases"][name] = res
    print(f"[{name}] {'OK' if res.get('ok') else 'FAIL'} "
          f"{res.get('error') or [f.get('note') for f in res.get('figures', [])]}", flush=True)


def plan_file_case(path: Path, tools, report: dict) -> None:
    """Готовый план проекта из файла (FORLABS_PROBE_PLAN): собрать и снять все его оконные и консольные снимки —
    так на Windows проверяется проект, который исправила настоящая модель."""
    shooter = WindowsShooter()
    raw = json.loads(path.read_text(encoding="utf-8"))
    target = build("from_file", {**raw, "shots": []}, tools)
    for i, shot in enumerate(s for s in raw.get("shots", []) if s.get("mode") in ("window", "console")):
        name, t0, res = f"plan_file_shot_{i + 1}", time.time(), {}
        try:
            r = shooter.shoot(target, shot, SHOTS / f"win_{name}.png")
            res.update(ok=r.ok, reason=r.reason, kind=r.kind, sandbox=r.sandbox, seen=r.seen[:800], ui=r.ui[:2500])
        except Exception as e:  # noqa: BLE001
            res.update(ok=False, error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-1500:])
        res["sec"] = round(time.time() - t0, 1)
        report["cases"][name] = res
        print(f"[{name}] {'OK' if res.get('ok') else 'FAIL'} {res.get('reason') or res.get('error') or ''}", flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    tools = languages.detect()
    report: dict = {"tools": {k: [t.path, t.version] for k, t in tools.items() if k == "dotnet"}, "cases": {}}
    try:
        direct_cases(tools, report)
    except Exception as e:  # noqa: BLE001
        report["cases"]["direct"] = {"ok": False, "error": f"{type(e).__name__}: {e}",
                                     "trace": traceback.format_exc()[-2500:]}
    repair_case(report)
    extra = os.environ.get("FORLABS_PROBE_PLAN")
    if extra:
        try:
            plan_file_case(ROOT / extra, tools, report)
        except Exception as e:  # noqa: BLE001
            report["cases"]["plan_file"] = {"ok": False, "error": f"{type(e).__name__}: {e}",
                                            "trace": traceback.format_exc()[-2500:]}
    import locale

    report["locale"] = locale.getlocale()[0]
    (OUT / "windows.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = [k for k, v in report["cases"].items() if not v.get("ok")]
    print("НЕ ПРОШЛИ:", bad, flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
