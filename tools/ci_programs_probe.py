"""Windows (CI): лаба из нескольких программ — библиотека классов, приложение с формой и консольная программа.

План взят таким, каким его составила модель для лабы «Разработка библиотеки динамической компоновки»
(tools/ci_programs_plan.json). Проверяется то, что получит студент:
- каждая программа — свой .csproj в своей папке, общий .sln собирается и ForLabs, и MSBuild из Visual Studio;
- снимок окна делает приложение с формой, снимок консоли — консольная программа: каждая запускается отдельно;
- тот же проект, записанный прежней версией одним проектом (так он не собирался: CS0017), делится на программы сам.
Отчёт: build/probe/programs.json, снимки: build/probe/shots/programs_*.png."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forlabs.pipeline import project as proj  # noqa: E402
from forlabs.runner import languages  # noqa: E402
from forlabs.runner.plan import ProjectPlan, validate  # noqa: E402

OUT = Path("build/probe").resolve()
SHOTS = OUT / "shots"
WORK = OUT / "programs-work"
PLAN = json.loads((ROOT / "tools" / "ci_programs_plan.json").read_text(encoding="utf-8"))


def prepare(name: str, raw: dict, tools):
    """План → папка проекта → сборка. → (план, папка, итог сборки)."""
    p = proj._tidy(ProjectPlan.model_validate(raw))
    if problems := validate(p):
        raise RuntimeError("план: " + "; ".join(problems))
    work = WORK / name
    shutil.rmtree(work, ignore_errors=True)
    languages.materialize(p, work, tools)
    b = languages.build(work, p, tools, 900)
    if not b.ok:
        raise RuntimeError("сборка: " + b.log[-2500:])
    return p, work, b


def shoot_all(name: str, p: ProjectPlan, work: Path, b, tools, res: dict) -> None:
    from forlabs.runner.shots import RunTarget, WindowsShooter

    shooter = WindowsShooter()
    res["shots"] = {}
    for shot in p.shots:
        prog = p.program_of(shot)
        spec = languages.run_spec_for(p, b, work, tools, prog.name)
        target = RunTarget(cmd=list(spec.cmd), cwd=Path(spec.cwd), console=spec.console, env=dict(spec.env or {}))
        out = SHOTS / f"programs_{name}_{prog.name}.png"
        r = shooter.shoot(target, shot, out)
        res["shots"][prog.name] = {"ok": r.ok, "reason": r.reason, "exe": Path(spec.cmd[0]).name, "mode": shot.mode,
                                   "seen": (r.seen or "")[-400:], "sandbox": r.sandbox}
    bad = [f"{k}: {v['reason']}" for k, v in res["shots"].items() if not v["ok"]]
    if bad:
        raise RuntimeError("снимки: " + "; ".join(bad))
    exes = {v["exe"].lower() for v in res["shots"].values()}
    if len(exes) != len(res["shots"]):
        raise RuntimeError(f"снимки сняты с одной и той же программы: {sorted(exes)}")


def case_model_plan(tools, res: dict) -> None:
    p, work, b = prepare("model", PLAN, tools)
    res["programs"] = {x.name: [x.kind, x.uses] for x in p.programs}
    res["artifacts"] = {k: v.name for k, v in b.artifacts.items()}
    top = sorted(x.name for x in work.iterdir() if x.is_file())
    res["top_files"] = top
    if [x for x in top if x.endswith(".csproj")] or len([x for x in top if x.endswith(".sln")]) != 1:
        raise RuntimeError(f"в корне проекта должен быть один .sln и ни одного .csproj: {top}")
    for prog in p.programs:
        if not (work / prog.name / f"{prog.name}.csproj").is_file():
            raise RuntimeError(f"нет {prog.name}/{prog.name}.csproj")
    shoot_all("model", p, work, b, tools, res)


def case_old_single_project(tools, res: dict) -> None:
    """Как лежало у студента в 1.0.12: три папки и один .csproj на всё. programs в плане нет."""
    raw = {k: v for k, v in PLAN.items() if k != "programs"}
    raw["shots"] = [{k: v for k, v in s.items() if k != "program"} for s in PLAN["shots"]]
    p, work, b = prepare("old", raw, tools)
    res["programs"] = {x.name: [x.kind, x.uses] for x in p.programs}
    want = {"ClassLibrary1": "library", "CalculatorApp": "winforms", "LibraryTestApp": "console"}
    got = {x.name: x.kind for x in p.programs}
    if got != want:
        raise RuntimeError(f"программы определены не так: {got}")
    res["shot_programs"] = {f"{s.section}.{s.block}": s.program for s in p.shots}
    shoot_all("old", p, work, b, tools, res)


def case_visual_studio_builds_the_solution(tools, res: dict) -> None:
    """Тот же .sln собирает MSBuild из Visual Studio — им студент и будет его открывать."""
    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    if not vswhere.is_file():
        res["skipped"] = "Visual Studio на этой машине нет"
        return
    found = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires", "Microsoft.Component.MSBuild",
                            "-find", r"MSBuild\**\Bin\MSBuild.exe"], capture_output=True, text=True, timeout=120)
    msbuild = next((ln.strip() for ln in found.stdout.splitlines() if ln.strip().lower().endswith("msbuild.exe")), "")
    if not msbuild:
        res["skipped"] = "MSBuild из Visual Studio не найден"
        return
    p = proj._tidy(ProjectPlan.model_validate(PLAN))
    work = WORK / "vs"
    shutil.rmtree(work, ignore_errors=True)
    languages.materialize(p, work, tools)
    sln = next(work.glob("*.sln"))
    r = subprocess.run([msbuild, sln.name, "-restore", "-nologo", "-v:m", "-p:Configuration=Debug"], cwd=work,
                       capture_output=True, text=True, timeout=900, encoding="utf-8", errors="replace")
    res.update(msbuild=msbuild, code=r.returncode, tail=(r.stdout + r.stderr)[-1200:])
    if r.returncode != 0:
        raise RuntimeError("MSBuild из Visual Studio не собрал .sln")
    exe = work / "CalculatorApp" / "bin" / "Debug"
    if not list(exe.glob("*/CalculatorApp.exe")):
        raise RuntimeError("после сборки в Visual Studio нет CalculatorApp.exe")
    first = next(ln for ln in sln.read_text(encoding="utf-8-sig").splitlines() if ln.startswith("Project("))
    res["startup_project"] = first.split('"')[3]
    if res["startup_project"] != "CalculatorApp":
        raise RuntimeError(f"первым в .sln должен идти запускаемый проект, а идёт {res['startup_project']}")


def main() -> int:
    for d in (OUT, SHOTS, WORK):
        d.mkdir(parents=True, exist_ok=True)
    tools = languages.detect()
    report: dict = {"tools": {k: t.version for k, t in tools.items()}, "cases": {}}
    for fn in (case_model_plan, case_old_single_project, case_visual_studio_builds_the_solution):
        name, res, t0 = fn.__name__.removeprefix("case_"), {}, time.time()
        try:
            fn(tools, res)
            res["ok"] = True
        except Exception as e:  # noqa: BLE001
            res.update(ok=False, error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-1500:])
        res["sec"] = round(time.time() - t0, 1)
        report["cases"][name] = res
        print(f"[{name}] {'OK' if res['ok'] else 'FAIL'} {json.dumps(res, ensure_ascii=False)[:1500]}", flush=True)
    (OUT / "programs.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = [k for k, v in report["cases"].items() if not v.get("ok")]
    print("НЕ ПРОШЛИ:", bad, flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
