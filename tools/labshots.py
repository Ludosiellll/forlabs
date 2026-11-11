"""Снимки окон программ лабы на настоящей Windows (временная ветка labshots).
Для каждого плана labshots/plans/*.json: проект как у ForLabs → сборка → снимок каждого рисунка WindowsShooter.
Результат — labshots/out/<план>/: shot_<раздел>_<блок>.png, results.json и project/ (исходники, .csproj, .sln)."""

from __future__ import annotations

import json
import shutil
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forlabs.pipeline import project as proj  # noqa: E402
from forlabs.runner import languages  # noqa: E402
from forlabs.runner.plan import ProjectPlan, validate  # noqa: E402
from forlabs.runner.shots import RunTarget, WindowsShooter  # noqa: E402

OUT = ROOT / "labshots" / "out"
WORK = Path("build/labshots").resolve()


def run_plan(name: str, raw: dict, tools) -> dict:
    res: dict = {"shots": {}}
    p = proj._tidy(ProjectPlan.model_validate(raw))
    if problems := validate(p):
        raise RuntimeError("план: " + "; ".join(problems))
    work = WORK / name
    shutil.rmtree(work, ignore_errors=True)
    languages.materialize(p, work, tools)
    out = OUT / name
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(work, out / "project")
    b = languages.build(work, p, tools, 900)
    res["build_ok"] = b.ok
    if not b.ok:
        res["build_log"] = b.log[-4000:]
        return res
    shooter = WindowsShooter()
    for shot in p.shots:
        key = f"{shot.section}_{shot.block}"
        try:
            prog = p.program_of(shot)
            spec = languages.run_spec_for(p, b, work, tools, prog.name if prog else "")
            target = RunTarget(cmd=list(spec.cmd), cwd=Path(spec.cwd), console=spec.console,
                               env=dict(spec.env or {}))
            r = shooter.shoot(target, shot, out / f"shot_{key}.png")
            res["shots"][key] = {"ok": r.ok, "reason": r.reason, "seen": (r.seen or "")[-1500:],
                                 "ui": (getattr(r, "ui", "") or "")[-1500:]}
        except Exception as e:  # noqa: BLE001
            res["shots"][key] = {"ok": False, "reason": f"{type(e).__name__}: {e}",
                                 "trace": traceback.format_exc()[-1500:]}
        print(name, key, res["shots"][key]["ok"], res["shots"][key]["reason"], flush=True)
    return res


def main() -> int:
    tools = languages.detect()
    print("инструменты:", {k: t.version for k, t in tools.items()}, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for f in sorted((ROOT / "labshots" / "plans").glob("*.json")):
        name = f.stem
        try:
            res = run_plan(name, json.loads(f.read_text(encoding="utf-8")), tools)
        except Exception as e:  # noqa: BLE001
            res = {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]}
        (OUT / name).mkdir(parents=True, exist_ok=True)
        (OUT / name / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                                 encoding="utf-8")
        print(name, json.dumps({k: v for k, v in res.items() if k != "shots"}, ensure_ascii=False)[:500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
