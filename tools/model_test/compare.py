"""Прогон лаб через ForLabs (lab_run.py) на моделях провайдера; итог — OUT/summary.md и summary.json.

    python tools/model_test/compare.py OUT_DIR --models qwen-ng --labs des,json,sql9,sql7 --runs 2 --parallel 4

Ключ — из переменной окружения, имя которой задано в key_env; в журналы не пишется."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
LABS_IN = Path(os.environ.get("LABS_IN", "labs_in"))
ADAPT = os.environ.get("NG_ADAPT", "dashscope")

MODELS = {
    "qwen-ng": dict(base="https://2.27.20.129/w/v1", model="qwen3.8-max-0902", adapt=ADAPT, price=(2.00, 6.00),
                    key_env="NG_KEY"),
    "deepseek": dict(base="https://api.aikeysforyou.com/v1", model="deepseek-v4.1-flash-1017", adapt="",
                     price=(0.30, 1.20), key_env="DS_KEY"),
}
LABS = {
    "des": dict(src="zki5.pdf", args=["--subject", "zki", "--subject-name", "Защита компьютерной информации",
                                      "--subject-short", "ЗКИ", "--variant", "30", "--gender", "f"]),
    "json": dict(src="lab21.docx", args=["--subject", "kpiyap", "--variant", "30", "--gender", "f"]),
    "sql9": dict(src="lab9.docx", args=["--variant", "7", "--gender", "m"]),
    "sql7": dict(src="lab7.docx", args=["--variant", "7", "--gender", "m"]),
}


def run_one(out: Path, name: str, lab: str, n: int) -> dict:
    cfg, lb = MODELS[name], LABS[lab]
    dest = out / f"{name}__{lab}__{n}"
    env = dict(os.environ, FORLABS_BASE_URL=cfg["base"], FORLABS_MODEL=cfg["model"], FORLABS_ADAPT=cfg["adapt"],
               FORLABS_PRICE_IN=str(cfg["price"][0]), FORLABS_PRICE_OUT=str(cfg["price"][1]),
               FORLABS_REASONING=os.environ.get("NG_REASONING", "low"), FORLABS_ROOT=str(ROOT),
               FORLABS_API_KEY=os.environ[cfg["key_env"]])
    t = time.time()
    logp = out / f"{name}__{lab}__{n}.log"
    try:
        with open(logp, "w") as log:
            subprocess.run([sys.executable, str(HERE / "lab_run.py"), str(LABS_IN / lb["src"]), str(dest), *lb["args"]],
                           env=env, stdout=log, stderr=subprocess.STDOUT, timeout=5400, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        pass
    res = {"model": name, "lab": lab, "run": n, "wall": round(time.time() - t)}
    log = logp.read_text(encoding="utf-8", errors="replace")
    try:
        r = json.loads((dest / "result.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        res["status"] = f"нет result.json: {type(e).__name__}"
        res["tail"] = log[-1500:]
        return res
    calls = r.get("calls") or []
    out_tok = sum(c.get("out", 0) for c in calls)
    res.update(status=r.get("status"), error=(r.get("error") or "")[:200], usd=r.get("usd"),
               tokens=r.get("tokens"), out_tokens=out_tok,
               reasoning_tokens=sum(c.get("reasoning", 0) for c in calls), requests=r.get("requests"),
               model_s=r.get("model_seconds"), tps=round(out_tok / max(1, r.get("model_seconds") or 1), 1),
               call_errors=sum(1 for c in calls if "error" in c), fallbacks=r.get("think_fallbacks"),
               fixes=len(re.findall(r"Исправляю|исправляю", log)),
               shot_fails=len(re.findall(r"Снимок не получился", log)),
               auto=(re.findall(r"Снял сам: (\d+)", log)[-1:] or ["0"])[0],
               manual=(re.findall(r"нужно от тебя: (\d+)", log)[-1:] or ["0"])[0],
               findings=len(r.get("findings") or []),
               tasks=len(((r.get("spec") or {}).get("tasks")) or []),
               figures=len(r.get("figures") or []),
               shots_ok=sum(1 for f in r.get("figures") or [] if f.get("image")),
               listings=r.get("listings"), built=((r.get("project") or {}).get("built")))
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--models", default="qwen-ng")
    ap.add_argument("--labs", default="des,json,sql9,sql7")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--parallel", type=int, default=4)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(m, lab, n) for n in range(1, a.runs + 1) for lab in a.labs.split(",") for m in a.models.split(",")]
    results = []
    with cf.ThreadPoolExecutor(a.parallel) as ex:
        futs = [ex.submit(run_one, out, *j) for j in jobs]
        for f in cf.as_completed(futs):
            res = f.result()
            results.append(res)
            (out / "progress.txt").write_text(f"{len(results)}/{len(jobs)}\n", encoding="utf-8")
            (out / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps({k: v for k, v in res.items() if k != "tail"}, ensure_ascii=False), flush=True)
    cols = ["model", "lab", "run", "status", "tasks", "usd", "tokens", "out_tokens", "reasoning_tokens", "requests",
            "model_s", "wall", "tps", "call_errors", "fallbacks", "fixes", "built", "figures", "shots_ok",
            "shot_fails", "findings"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in sorted(results, key=lambda r: (r["model"], r["lab"], r["run"])):
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
