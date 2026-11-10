"""Снимки в программах: какие рисунки отчёта ForLabs сделает сам, работая в программе студента (агент
forlabs.agent), и сам этот проход — по кнопке «Сделать снимки в программах».

План — один запрос к модели на лабу: для каждого несделанного рисунка программа из списка, что в ней сделать и что
должно быть видно. Проход — по программам: программа открывается один раз, агент делает в ней все её рисунки по
очереди; студент может остановить (Esc, взял мышь) — оставшиеся рисунки ждут следующего нажатия."""

from __future__ import annotations

import logging
import sys
import uuid
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field, field_validator

from forlabs.core.models import AppTask, Figure, LabState

log = logging.getLogger("forlabs")


class AppShotItem(BaseModel):
    section: int
    block: int
    app: str = ""
    goal: str = ""
    shot: str = ""
    open: list[str] = Field(default_factory=list)
    allow: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("app", "goal", "shot", "reason", mode="before")
    @classmethod
    def _s(cls, v):
        return "" if v is None else str(v).strip()

    @field_validator("open", "allow", mode="before")
    @classmethod
    def _l(cls, v):
        items = v if isinstance(v, list) else ([v] if v else [])
        return [str(x).strip() for x in items if x is not None and str(x).strip()]


class AppShotPlan(BaseModel):
    items: list[AppShotItem] = Field(default_factory=list)


PLAN_SYSTEM = """Ты помогаешь оформить отчёт по лабораторной. Часть рисунков — снимки окон программ. ForLabs умеет
сам работать в программе на компьютере студента: открыть её (с файлами лабы), щёлкать, набирать, снять окно.
Для каждого рисунка из списка реши, может ли ForLabs снять его сам, и если да — в какой программе и что сделать.

Ответ — только JSON: {"items": [{"section": N, "block": M, "app": "ключ программы или пусто",
"goal": "что сделать в программе: конкретно, по шагам (что открыть, что набрать, куда нажать)",
"shot": "что должно быть видно на снимке", "open": ["файлы лабы, которые открыть при запуске"],
"allow": ["слова на кнопках вроде «Удалить», если они нужны по заданию"], "reason": "если app пусто — почему"}]}

Правила:
- app — только ключ из списка программ (можно и не установленную: студенту предложат её поставить).
- open — только файлы из списка файлов лабы (пути как в списке); нет подходящего — пустой список.
- Окно консольной программы, страница сайта и окно своей программы лабы ForLabs снимает отдельно — для них app пусто,
  reason «снимок программы лабы».
- app пусто и для того, что нельзя сделать в окне программы: фото, рисунок от руки, экран телефона, сайт со входом,
  оборудование, подпись преподавателя.
- goal и shot — по тексту лабы и подписи рисунка; данные для ввода бери из отчёта (те же числа и названия)."""


def _blocks(st: LabState):
    if st.report:
        for sec in st.report.sections:
            for bi, b in enumerate(sec.blocks, start=1):
                yield sec.n, bi, b


def hand_in_dir(pipe, st: LabState) -> Path:
    """Папка сдачи лабы (там проект программы и файлы, которые агенту можно открывать и сохранять)."""
    from forlabs.core.store import safe_name
    from forlabs.core.subjects import get_subject
    from forlabs.pipeline.run import output_root

    profile = pipe._profile(st)
    short = safe_name(get_subject(st.subject_id).short_name)
    number = safe_name(st.spec.number if st.spec else st.number)
    return output_root(profile) / short / f"ЛР{number}"


def lab_files(pipe, st: LabState, limit: int = 60) -> list[str]:
    root = hand_in_dir(pipe, st)
    if not root.is_dir():
        return []
    skip = {"obj", "bin", "out", "__pycache__", ".vs", "node_modules", ".git"}
    out = []
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root)
        if f.is_file() and not (set(rel.parts[:-1]) & skip) and not f.name.startswith("~$"):
            out.append(rel.as_posix())
        if len(out) >= limit:
            break
    return out


def pending(st: LabState) -> list[tuple[int, int, Figure]]:
    """Рисунки без картинки, которые ещё никто не взялся сделать (ни проект, ни агент)."""
    return [(n, bi, b) for n, bi, b in _blocks(st)
            if isinstance(b, Figure) and not b.image_path and b.app_task is None
            and not (b.auto_note or "").startswith(("Сам не снял", "Программа не собралась", "Для снимка нужна"))]


def plan(pipe, st: LabState, llm, emit=None) -> LabState:
    """Решить, какие рисунки ForLabs снимет в программах. Модель — один запрос; ошибка — не ошибка лабы."""
    from forlabs.agent import apps
    from forlabs.pipeline.project import lab_text

    todo = pending(st)
    if not todo:
        return st
    custom = getattr(pipe, "app_paths", {}) or {}
    have = apps.installed(custom) if sys.platform == "win32" else {}
    catalog = "\n".join(f"- {k}: {a.title}" + (" (установлена)" if k in have else " (не установлена)")
                        for k, a in apps.APPS.items())
    files = lab_files(pipe, st)
    figs = "\n".join(f"- section {n}, block {bi}: «{b.topic}» — {b.slot or ''}" for n, bi, b in todo)
    user = (f"Лаба:\n{lab_text(st)[:12000]}\n\nПрограммы:\n{catalog}\n\nФайлы лабы:\n"
            + ("\n".join(f"- {f}" for f in files) or "(нет)") + f"\n\nРисунки:\n{figs}")
    try:
        got = llm.json([{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": user}], AppShotPlan,
                       max_tokens=4000)
    except Exception as e:  # noqa: BLE001 — снимки сделает студент, как раньше
        log.warning("план снимков в программах: %s", e)
        return st
    want = {(n, bi): b for n, bi, b in todo}
    allowed = set(files)
    planned = 0
    for it in got.items:
        b = want.get((it.section, it.block))
        if b is None or it.app not in apps.APPS or not it.goal:
            continue
        opens = [o for o in (PurePosixPath(x.replace("\\", "/")).as_posix().lstrip("./") for x in it.open)
                 if o in allowed][:5]
        b.app_task = AppTask(app=it.app, goal=it.goal[:1500], shot=it.shot[:600], open=opens, allow=it.allow[:5])
        b.auto_note = note_for(b, have)
        planned += 1
    if planned and emit:
        emit(f"Рисунков, которые ForLabs снимет сам в программах: {planned}")
    return st


def note_for(b: Figure, have: dict[str, str]) -> str:
    from forlabs.agent import apps

    app = apps.APPS[b.app_task.app]
    if b.app_task.app in have or sys.platform != "win32":
        return f"Сниму сам в программе «{app.title}»: нажми «Сделать снимки в программах»."
    b.need_tool = f"app:{app.key}"
    return (f"Для снимка нужна программа «{app.title}»: поставь её или укажи, где она, и нажми «Сделать снимки "
            "в программах».")


def run(pipe, st: LabState, llm, emit=None, before=None, after=None, desk_factory=None, watch_factory=None,
        finder=None) -> LabState:
    """Проход агента по рисункам с app_task. before/after — свернуть и вернуть окно ForLabs; desk_factory и
    watch_factory и finder подменяются в тестах. Меняет st (картинки, пометки)."""
    from forlabs.agent import apps, core

    emit = emit or (lambda text, kind="info": None)
    todo = [(n, bi, b) for n, bi, b in _blocks(st) if isinstance(b, Figure) and b.app_task and not b.image_path]
    if not todo:
        emit("Снимков в программах не осталось")
        return st
    if sys.platform != "win32" and desk_factory is None:
        for _, _, b in todo:
            b.auto_note = "Снимки в программах ForLabs делает только в Windows."
        return st
    custom = getattr(pipe, "app_paths", {}) or {}
    folder = hand_in_dir(pipe, st)
    folder.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list] = {}
    for item in todo:
        groups.setdefault(item[2].app_task.app, []).append(item)
    lab = pipe.lab_dir(st)
    stopped = None
    from forlabs.runner.shots import DESKTOP

    if not DESKTOP.acquire(timeout=5):
        emit("Жду, пока ForLabs закончит снимать окна в другой лабе")
        if not DESKTOP.acquire(timeout=900):
            emit("Мышь и клавиатура заняты другой лабой — нажми «Сделать снимки в программах» позже", "warn")
            return st
    if before:
        before()
    try:
        for key, items in groups.items():
            if stopped:
                break
            app = apps.APPS[key]
            exe = (finder or apps.find)(key, custom)
            if not exe:
                for _, _, b in items:
                    b.need_tool = f"app:{key}"
                    b.auto_note = (f"Для снимка нужна программа «{app.title}»: поставь её или укажи, где она, и нажми "
                                   "«Сделать снимки в программах».")
                emit(f"Нет программы «{app.title}»", "warn")
                continue
            files = []
            for _, _, b in items:
                files += [folder / f for f in b.app_task.open if (folder / f).is_file() and folder / f not in files]
            watch = (watch_factory or _watch)(app.title)
            try:
                emit(f"Открываю «{app.title}»")
                try:
                    desk = (desk_factory or _desk)(key, exe, files, folder, watch)
                except Exception as e:  # noqa: BLE001 — AppError и всё, что помешало запуску
                    reason = str(e).rstrip(".")
                    for _, _, b in items:
                        b.auto_note = f"Сам не снял: {reason}."
                    emit(f"«{app.title}»: {reason}", "warn")
                    if getattr(watch, "reason", None):
                        stopped = watch.reason
                    continue
                for n, bi, b in items:
                    if stopped:
                        break
                    t = b.app_task
                    hints = list(app.hints) + ([f"файлы лабы открыты: {', '.join(t.open)}"] if t.open else [])
                    task = core.Task(goal=t.goal, shot=t.shot or b.topic, app=app.title, hints=hints,
                                     folder=str(folder), allow=t.allow, kind=app.kind)
                    emit(f"Рисунок «{b.topic}»: работаю в «{app.title}»")

                    def say(text, w=watch):
                        if hasattr(w, "say"):
                            w.say(text)
                        emit(text)

                    try:
                        res = core.run(task, desk, llm, on_step=say,
                                       should_stop=lambda w=watch: bool(getattr(w, "reason", None)))
                        if res.ok and res.image is not None:
                            rel = f"assets/app_{n}_{bi}_{uuid.uuid4().hex[:6]}.png"
                            (lab / rel).parent.mkdir(parents=True, exist_ok=True)
                            res.image.save(lab / rel)
                            b.image_path, b.auto_note, b.need_tool = rel, None, None
                            try:
                                pipe._sync(st, [rel])
                            except Exception:  # noqa: BLE001 — облако догонит при следующем сохранении
                                pass
                            emit(f"Снял: «{b.topic}»", "done")
                            continue
                    except Exception as e:  # noqa: BLE001 — один рисунок не должен сорвать остальные
                        log.warning("агент: рисунок «%s»: %s", b.topic, e, exc_info=True)
                        res = core.Result(False, reason=f"сбой ({type(e).__name__}: {e})")
                    if getattr(watch, "reason", None):  # остановил студент: не ошибка, продолжим по кнопке
                        b.auto_note = (f"Остановлено: {watch.reason}. Нажми «Сделать снимки в программах», чтобы "
                                       "продолжить.")
                    else:
                        b.auto_note = f"Сам не снял: {res.reason}."
                        emit(f"Не снял «{b.topic}»: {res.reason}", "warn")
                    if getattr(watch, "reason", None):
                        stopped = watch.reason
            finally:
                if hasattr(watch, "stop"):
                    watch.stop()
    finally:
        DESKTOP.release()
        if after:
            after()
    if stopped:
        emit(f"{stopped[0].upper()}{stopped[1:]}. Остальные снимки — по кнопке «Сделать снимки в программах».", "warn")
    st.docx_sha = None
    return st


def _watch(title: str):
    from forlabs.agent.watch import Watch

    w = Watch(title)
    w.start()
    return w


def _desk(key, exe, files, folder, watch):
    from forlabs.agent.windesk import WinDesk

    return WinDesk(key, exe, files, folder, watch=watch)
