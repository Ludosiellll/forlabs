"""Полный прогон ForLabs на настоящей методичке и настоящей модели (ключ — из окружения FORLABS_API_KEY,
FORLABS_BASE_URL, FORLABS_MODEL; сам ключ нигде не печатается).

    python lab_run.py МЕТОДИЧКА.docx ПАПКА_РЕЗУЛЬТАТА [--variant N] [--gender m|f] [--stop parse]
                      [--chat "сообщение"]... [--fill] [--wishes "текст"] [--no-project]

Что делает: чистый дом ForLabs в ПАПКА/home, вымышленный профиль, свой предмет «СУБД», разбор → подтверждение →
выполнение (текст, проверка, проект программы, сборка, снимки консоли в псевдотерминале Linux) → вёрстка.
В ПАПКА остаются: result.json (всё по лабе), report.txt (отчёт словами), journal.txt, plan.json, скриншоты и docx.
Окна (WinForms, Workbench и т.п.) в Linux не снимаются — такие рисунки остаются «нужно от тебя» с причиной."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("source")
ap.add_argument("out")
ap.add_argument("--variant", type=int, default=7)
ap.add_argument("--gender", default="f")
ap.add_argument("--stop", default="")
ap.add_argument("--chat", action="append", default=[])
ap.add_argument("--fill", action="store_true")
ap.add_argument("--wishes", default="")
ap.add_argument("--no-project", action="store_true")
ap.add_argument("--subject", default="subd")
ap.add_argument("--subject-name", default="")
ap.add_argument("--subject-short", default="")
ap.add_argument("--subject-kind", default="code")
args = ap.parse_args()

OUT = Path(args.out).resolve()
shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)
os.environ["FORLABS_HOME"] = str(OUT / "home")
ROOT = Path(os.environ.get("FORLABS_ROOT", "/home/user/forlabs"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import eval_projects as E  # noqa: E402

from forlabs.core.llm import LLM  # noqa: E402
from forlabs.core.models import Enrollment, Figure, Listing, Profile  # noqa: E402
from forlabs.core.settings import Settings  # noqa: E402
from forlabs.core.store import Store  # noqa: E402
from forlabs.core.subjects import Subject, add_custom_subject, all_subjects  # noqa: E402
from forlabs.pipeline.chat import describe_block  # noqa: E402
from forlabs.pipeline.memory import SubjectMemory  # noqa: E402
from forlabs.pipeline.run import Pipeline  # noqa: E402
from forlabs.render.conformance import compare  # noqa: E402
from forlabs.runner.shots import ShotResult  # noqa: E402

JOURNAL: list[str] = []
CALLS: list[dict] = []  # каждый запрос к модели: время, токены, цена


def adapt_provider(llm) -> None:
    """Параметр рассуждения ForLabs (reasoning_effort) — в форму провайдера; учёт времени, токенов и цены.
    FORLABS_ADAPT: "" — как есть (DeepSeek), zai — GLM 5.3 (рассуждение не выключается), dashscope — Qwen
    (enable_thinking), openrouter — reasoning.effort. Цена: из usage.cost, иначе FORLABS_PRICE_IN/OUT ($ за 1M)."""
    adapt = os.environ.get("FORLABS_ADAPT", "")
    pin, pout = float(os.environ.get("FORLABS_PRICE_IN", "0")), float(os.environ.get("FORLABS_PRICE_OUT", "0"))
    orig = llm._post

    def post(payload):
        p = dict(payload)
        effort = p.pop("reasoning_effort", None)
        if adapt == "zai":  # у GLM 5.3 рассуждение есть всегда: даём ему место в пределе ответа
            p["thinking"] = {"type": "enabled"}
            p["reasoning_effort"] = effort or "low"
            p["max_tokens"] = max(int(p.get("max_tokens") or 0), 16000)
        elif adapt == "dashscope":
            p["enable_thinking"] = bool(effort)
            if effort:
                p["max_tokens"] = max(int(p.get("max_tokens") or 0), 16000)
        elif adapt == "openrouter":
            if effort:
                p["reasoning"] = {"effort": effort}
        elif effort:
            p["reasoning_effort"] = effort
        t = time.time()
        try:
            d = orig(p)
        except Exception as e:
            CALLS.append({"s": round(time.time() - t, 1), "error": str(e)[:200], "think": bool(effort)})
            raise
        u = d.get("usage") or {}
        det = u.get("completion_tokens_details") or {}
        pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        cost = u.get("cost")
        if not isinstance(cost, (int, float)):
            cost = (pt * pin + ct * pout) / 1e6
        CALLS.append({"s": round(time.time() - t, 1), "in": pt, "out": ct, "think": bool(effort),
                      "reasoning": int(u.get("completion_thinking_tokens") or det.get("reasoning_tokens") or 0),
                      "finish": ((d.get("choices") or [{}])[0]).get("finish_reason"), "usd": round(cost, 5)})
        return d

    llm._post = post


def say(text: str) -> None:
    print(text, flush=True)
    JOURNAL.append(text)


def screen_png(text: str, out: Path) -> None:
    """Экран консоли картинкой (чтобы в отчёте был настоящий вывод программы)."""
    lines = text.rstrip("\n").split("\n")[-45:]
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 15)
    except OSError:
        font = ImageFont.load_default()
    w = max(640, min(1500, 9 * max((len(x) for x in lines), default=40) + 30))
    im = Image.new("RGB", (w, 20 * len(lines) + 30), (12, 12, 12))
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text((12, 12 + 20 * i), ln[:200], fill=(204, 204, 204), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)


class TtyShooter:
    """Снимки консольных программ в Linux: программа в псевдотерминале, ввод сценария, проверка expect."""
    container = True

    def __init__(self):
        self.calls: list[dict] = []

    def shoot(self, target, shot, out: Path) -> ShotResult:
        rec = {"mode": shot.mode, "program": getattr(shot, "program", ""), "input": shot.input, "expect": shot.expect}
        self.calls.append(rec)
        if shot.mode == "browser":
            return self._browser(target, shot, out, rec)
        if shot.mode != "console" or not target.cmd:
            rec["result"] = "окно: в Linux-стенде не снимается"
            return ShotResult(False, reason="этот стенд (Linux) снимает только консоль и страницы сайта; окна "
                                            "программ проверяются в Windows")
        env = {**os.environ, **(target.env or {})}
        env.pop("MYSQL_PWD", None)
        res = E.run_tty(list(target.cmd), Path(target.cwd), env, E.shot_input(shot), crlf=False)
        screen = res["screen"]
        rec["screen"] = screen[-3000:]
        if shot.expect and shot.expect not in screen:
            rec["result"] = "expect не найден"
            return ShotResult(False, reason=f"программа вывела не то, что ожидалось («{shot.expect}»)", kind="expect",
                              seen=screen[-2500:])
        screen_png(screen, out)
        rec["result"] = "снят"
        return ShotResult(True, image=out, seen=screen[-2500:])


def _browser(self, target, shot, out: Path, rec: dict) -> ShotResult:
    """Страница сайта в Chromium: открыть, пройти шаги (set id:…, select, click по тексту), проверить expect."""
    import subprocess

    from playwright.sync_api import sync_playwright

    server = None
    url = shot.url or target.url or ""
    rec["url"] = url
    try:
        if target.server:
            server = subprocess.Popen(target.server, cwd=str(target.cwd), stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
            time.sleep(1.5)
        with sync_playwright() as pw:
            b = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium"))
            pg = b.new_page(viewport={"width": 1200, "height": 700})
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
            pg.goto(url, timeout=20000)
            pg.wait_for_timeout(700)
            # те же шаги и та же проверка «что видно на экране», что в приложении (forlabs.runner.webdrive)
            from forlabs.runner import webdrive
            from forlabs.runner.shots import shown, typed_text

            try:
                webdrive.perform(pg, shot.steps)
            except webdrive.StepError as e:
                state = webdrive.page_state(pg)
                rec["result"], rec["screen"] = f"шаг не выполнился: {e}", state[-3000:]
                b.close()
                return ShotResult(False, reason=str(e), seen=state[-2500:], kind="ui", ui=state[-2500:])
            found, text = webdrive.check_expect(pg, shot.expect, shot.steps, typed_text(shot))
            rec["screen"], rec["page_errors"] = text[-3000:], errors
            if shot.expect and not found:
                rec["result"] = "expect не найден"
                b.close()
                return ShotResult(False, reason=f"на странице нет ожидаемого «{shot.expect}»", kind="expect",
                                  seen=text[-2500:])
            out.parent.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(out))
            b.close()
        rec["result"] = "снят"
        return ShotResult(True, image=out, seen=text[-2500:])
    except Exception as e:  # noqa: BLE001
        rec["result"] = f"ошибка браузера: {type(e).__name__}: {str(e)[:300]}"
        return ShotResult(False, reason=f"страница не открылась или шаг не выполнился: {str(e)[:200]}", kind="ui")
    finally:
        if server is not None:
            server.kill()


TtyShooter._browser = _browser

# В Linux-стенде Edge нет: страницы снимает Chromium Playwright, поэтому «браузер найден» сообщаем сами
from forlabs.runner import languages as _L  # noqa: E402

_real_detect = _L.detect


def _detect_with_browser(*a, **k):
    tools = dict(_real_detect(*a, **k))
    tools.setdefault("edge", _L.Toolchain("edge", os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium"), "стенд"))
    return tools


_L.detect = _detect_with_browser


def main() -> None:
    src = Path(args.source)
    if args.subject not in all_subjects():  # своего предмета в приложении нет — заводим, как сделал бы студент
        name = args.subject_name or "Системы управления базами данных"
        short = args.subject_short or "СУБД"
        add_custom_subject(Subject(id=args.subject, name=name, short_name=short, kind=args.subject_kind,
                                   default_header_style="long", default_variant=args.variant))
    from forlabs.core.subjects import get_subject

    subject = get_subject(args.subject)
    store = Store()
    female = args.gender == "f"
    pid = "demo"
    store.save_profile(Profile(
        id=pid, full_name="Соколова Мария Андреевна" if female else "Соколов Марк Андреевич",
        title_name="М.А. Соколова" if female else "М.А. Соколов", gender=args.gender, group="РС02-24",
        doc_author="Соколова Мария" if female else "Соколов Марк", output_root=str(OUT / "сдача"),
        enrollments=[Enrollment(subject_id=subject.id, title_subject=subject.name, teacher="Т.Т. Тестова",
                                academic_year="2026/2027", variant=args.variant,
                                header_style=subject.default_header_style)]))
    settings = Settings(mode="local", base_url=os.environ["FORLABS_BASE_URL"], model=os.environ["FORLABS_MODEL"],
                        reasoning=os.environ.get("FORLABS_REASONING", "low"))
    llm = LLM(settings, api_key=os.environ["FORLABS_API_KEY"])
    adapt_provider(llm)
    pipe = Pipeline(store, llm_factory=lambda: llm, prefer_word=False, memory=SubjectMemory())
    pipe.projects = not args.no_project
    shooter = TtyShooter()
    pipe.shooter = shooter
    pipe.bus.publish = (lambda orig: (lambda key, text, kind="info", step=None:
                                      (say(f"  [{kind}] {text}"), orig(key, text, kind, step))[1]))(pipe.bus.publish)
    t0 = time.time()
    st = pipe.create_lab(pid, subject.id, [(src.name, src.read_bytes())])
    st = pipe.parse(st)
    if st.status == "failed":
        say(f"РАЗБОР НЕ УДАЛСЯ: {st.error}")
        dump(pipe, st, llm, shooter, t0)
        sys.exit(2)
    say(f"РАЗБОР ({time.time() - t0:.0f} с): ЛР {st.spec.number} «{st.spec.topic}»; вариант {st.spec.variant}")
    for t in st.spec.tasks:
        say(f"   {t.n}. [{t.kind}] {t.text[:300]}")
    say(f"   вопросов: {len(st.spec.questions)} | замечания разбора: {[(f.rule_id, f.message[:90]) for f in st.findings]}")
    if args.stop == "parse":
        dump(pipe, st, llm, shooter, t0)
        return
    if args.wishes:
        st.spec.wishes = args.wishes
    st = pipe.execute(pipe.confirm(st, st.spec if args.wishes else None))
    say(f"ВЫПОЛНЕНИЕ ({time.time() - t0:.0f} с): статус {st.status}" + (f", ошибка: {st.error}" if st.error else ""))
    for msg in args.chat:
        say(f"\nЧАТ > {msg}")
        try:
            st = pipe.chat(st, msg)
            say(f"ЧАТ < [{st.chat[-1].kind}] {st.chat[-1].text}")
        except Exception as e:  # noqa: BLE001
            say(f"ЧАТ: ошибка {type(e).__name__}: {e}")
    if args.fill and st.status != "failed":
        for sec_n, blk, fig in pipe.slots(st):
            buf = OUT / "_fill.png"
            screen_png(f"[заглушка стенда] {fig.topic}", buf)
            st = pipe.attach_screenshot(st, sec_n, blk, buf.read_bytes())
    dump(pipe, st, llm, shooter, t0)


def dump(pipe, st, llm, shooter, t0) -> None:
    lab = pipe.lab_dir(st)
    info: dict = {"status": st.status, "error": st.error, "seconds": round(time.time() - t0),
                  "tokens": getattr(llm.usage, "total_tokens", None), "prompt_tokens": llm.usage.prompt_tokens, "completion_tokens": llm.usage.completion_tokens, "requests": llm.usage.requests, "think_fallbacks": getattr(llm, "think_fallbacks", 0), "calls": CALLS,
                  "usd": round(sum(c.get("usd", 0) for c in CALLS), 4),
                  "model_seconds": round(sum(c.get("s", 0) for c in CALLS), 1), "reasoning": llm.settings.reasoning, "lab_dir": str(lab), "docx": st.docx_path,
                  "pdf": st.pdf_path, "pages": len(st.pages or [])}
    if st.spec:
        info["spec"] = {"number": st.spec.number, "date": st.spec.date, "topic": st.spec.topic, "goal": st.spec.goal,
                        "equipment": getattr(st.spec, "equipment", ""), "variant": st.spec.variant,
                        "variant_text": st.spec.variant_text, "conclusion": getattr(st.spec, "conclusion", ""),
                        "tasks": [{"n": t.n, "kind": t.kind, "text": t.text} for t in st.spec.tasks],
                        "questions": list(st.spec.questions), "source_chars": len(st.spec.source_text or "")}
    info["findings"] = [{"rule": f.rule_id, "where": f.location, "message": f.message} for f in st.findings]
    lines = []
    if st.report:
        figures = []
        for sec in st.report.sections:
            lines.append(f"\n===== Задание {sec.n}. {sec.title}")
            for i, b in enumerate(sec.blocks, start=1):
                lines.append(describe_block(f"b{i}", b))
                if isinstance(b, Figure):
                    figures.append({"where": f"{sec.n}.{i}", "topic": b.topic, "slot": b.slot, "image": b.image_path,
                                    "note": b.auto_note, "need_tool": b.need_tool,
                                    "app_task": b.app_task.model_dump() if b.app_task else None})
        lines.append(f"\n===== Вывод\n{st.report.conclusion}")
        lines.append("\n===== Контрольные вопросы")
        lines += [f"{q.n}. {q.question}\n   Ответ: {q.answer}" for q in st.report.questions]
        info["figures"] = figures
        info["listings"] = sum(1 for s in st.report.sections for b in s.blocks if isinstance(b, Listing))
    (OUT / "report.txt").write_text("\n".join(lines), encoding="utf-8")
    info["project"] = st.project.model_dump() if st.project else None
    plan = lab / "project" / "plan.json"
    if plan.exists():
        shutil.copy2(plan, OUT / "plan.json")
    info["shots"] = shooter.calls
    info["chat"] = [{"role": m.role, "kind": m.kind, "text": m.text} for m in st.chat]
    if st.docx_path and Path(st.docx_path).exists():
        try:
            info["conformance"] = [str(x) for x in compare(st.docx_path)]
        except Exception as e:  # noqa: BLE001
            info["conformance"] = f"не сверено: {type(e).__name__}: {e}"
        shutil.copy2(st.docx_path, OUT / Path(st.docx_path).name)
    for rel in [f["image"] for f in info.get("figures", []) if f.get("image")]:
        if (lab / rel).exists():
            (OUT / "shots").mkdir(exist_ok=True)
            shutil.copy2(lab / rel, OUT / "shots" / Path(rel).name)
    (OUT / "result.json").write_text(json.dumps(info, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT / "journal.txt").write_text("\n".join(JOURNAL), encoding="utf-8")
    say(f"ИТОГ: статус {st.status}; токенов {info['tokens']}; время {info['seconds']} с; файлы — в {OUT}")
    if st.project:
        say(f"   проект: собран={st.project.built}, не хватает={st.project.missing}, заметка={st.project.note!r}")
    for f in info.get("figures", []):
        say(f"   рисунок {f['where']} «{f['topic'][:70]}»: " + ("СНЯТ" if f["image"] else f"нет — {f['note']}"))


if __name__ == "__main__":
    main()
