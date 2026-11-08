"""Локальный веб-интерфейс ForLabs (FastAPI + шаблоны)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.templating import Jinja2Templates

from forlabs.cloud.auth import CloudError
from forlabs.core.llm import LLMError
from forlabs.core.models import Enrollment, LabSpec, Profile, Task, finding_key, is_accepted
from forlabs.core.settings import API_PRESETS, Settings, get_api_key, save_settings, set_api_key
from forlabs.core.subjects import Subject, all_subjects, get_subject
from forlabs.pipeline.run import PipelineError, today
from forlabs.render import libreoffice, word_com
from forlabs.report.derive import numbered_blocks
from forlabs.report.morph import goal_to_conclusion
from forlabs.ui.admin_routes import access_text, ago, date_ru, labs_estimate, register_admin
from forlabs.ui.charts import fmt_tokens
from forlabs.ui.context import AppContext, slug

HERE = Path(__file__).parent
LOCAL_HOSTS = ["127.0.0.1", "localhost"]
STATUS_RU = {"draft": "Черновик", "parsed": "Проверь разбор", "confirmed": "Готова к выполнению",
             "executing": "Выполняется", "needs_manual": "Нужны скриншоты", "rendered": "Свёрстана",
             "final": "Сдана", "failed": "Ошибка"}
STEPS = [("parse", "Разбор методички"), ("write", "Текст отчёта"), ("check", "Проверка"), ("render", "Вёрстка"),
         ("finalize", "Доводка и предпросмотр")]


def _q(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")


def _same_origin(request: Request) -> bool:
    """POST с чужого сайта (форма на странице в интернете, отправленная на 127.0.0.1) не принимаем."""
    from urllib.parse import urlparse

    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return True  # не браузер: браузер всегда шлёт Origin у межсайтового POST
    return urlparse(source).netloc == request.headers.get("host", "")


def create_app(ctx: AppContext | None = None, allowed_hosts: list[str] | None = None) -> FastAPI:
    ctx = ctx or AppContext()
    app = FastAPI(title="ForLabs", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    tpl.env.globals.update(STATUS_RU=STATUS_RU, q=_q, finding_key=finding_key, date_ru=date_ru, ago=ago,
                           fmt_tokens=fmt_tokens, access_text=access_text, labs_estimate=labs_estimate)

    def render(request: Request, name: str, **kw) -> HTMLResponse:
        base = {"request": request, "ctx": ctx, "subjects": all_subjects(), "nav": kw.pop("nav", ""),
                "flash": request.query_params.get("msg"), "error": request.query_params.get("err")}
        if ctx.cloud and ctx.auth.session:
            base["user_email"] = ctx.auth.session.email
            try:
                base["member"] = ctx.store.member()
            except Exception:
                base["member"] = None
        base.update(kw)
        return tpl.TemplateResponse(request, name, base)

    def redirect(url: str, msg: str | None = None, err: str | None = None) -> RedirectResponse:
        sep = "&" if "?" in url else "?"
        if msg:
            url += f"{sep}msg={_q(msg)}"
            sep = "&"
        if err:
            url += f"{sep}err={_q(err)}"
        return RedirectResponse(url, status_code=303)

    def lab_url(p: str, s: str, n: str) -> str:
        return f"/labs/{_q(p)}/{_q(s)}/{_q(n)}"

    BUSY = "По этой лабе сейчас идёт работа — дождись, пока она закончится."

    def busy_lab(p: str, s: str, n: str) -> bool:
        return f"{p}/{s}/{n}" in ctx.running

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_origin(request):
            return HTMLResponse("Запрос пришёл с другого сайта и отклонён.", status_code=403)
        return await call_next(request)

    # Последним добавлен — значит, срабатывает первым: чужое имя хоста (DNS rebinding) отсекается сразу.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or LOCAL_HOSTS)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        open_paths = ("/login", "/register", "/static", "/settings")
        if ctx.cloud and not ctx.auth.session and not path.startswith(open_paths):
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    @app.exception_handler(CloudError)
    async def cloud_error(request: Request, exc: CloudError):
        if exc.message_ru.startswith("Нужно войти") or "войди" in exc.message_ru:
            return redirect("/login", err=exc.message_ru)
        return render(request, "error.html", message=exc.message_ru, detail=exc.detail)

    # вход
    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return render(request, "login.html")

    @app.post("/login")
    def login(email: str = Form(...), password: str = Form(...)):
        try:
            ctx.auth.sign_in(email, password)
        except CloudError as e:
            return redirect("/login", err=e.message_ru)
        ctx.reset()
        return redirect("/")

    @app.post("/register")
    def register(email: str = Form(...), password: str = Form(...), name: str = Form("")):
        try:
            msg = ctx.auth.sign_up(email, password, name)
        except CloudError as e:
            return redirect("/login?tab=register", err=e.message_ru)
        ctx.reset()
        return redirect("/" if ctx.auth.session else "/login", msg=msg)

    @app.post("/logout")
    def logout():
        ctx.auth.sign_out()
        ctx.reset()
        return redirect("/login")

    # главная
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, profile: str | None = None, subject: str | None = None):
        member = None
        if ctx.cloud:
            member = ctx.store.member()
            if member and not member.approved:
                return render(request, "pending.html", member=member)
            try:
                ctx.sync_subjects()
            except CloudError:
                pass  # без связи работают уже известные предметы
        profiles = ctx.store.list_profiles()
        if not profiles:
            return render(request, "home.html", profiles=[], current=None, nav="home")
        current = next((p for p in profiles if p.id == profile), profiles[0])
        enrolled = [e.subject_id for e in current.enrollments if e.subject_id in all_subjects()]
        subject_id = subject if subject in all_subjects() else (enrolled[0] if enrolled else next(iter(all_subjects())))
        labs = ctx.store.list_labs(current.id, subject_id)
        counts = {}
        for sid in enrolled:
            try:
                counts[sid] = len(ctx.store.list_labs(current.id, sid)) if sid != subject_id else len(labs)
            except Exception:
                counts[sid] = 0
        return render(request, "home.html", profiles=profiles, current=current, subject_id=subject_id,
                      enrolled=enrolled, labs=labs, counts=counts, nav="home")

    # профили
    @app.get("/profiles", response_class=HTMLResponse)
    def profiles_page(request: Request):
        return render(request, "profiles.html", profiles=ctx.store.list_profiles(), nav="profiles")

    @app.get("/profiles/new", response_class=HTMLResponse)
    def profile_new(request: Request):
        return render(request, "profile_edit.html", p=Profile(id="", full_name="", title_name="", enrollments=[]),
                      is_new=True, nav="profiles")

    @app.get("/profiles/{pid}", response_class=HTMLResponse)
    def profile_edit(request: Request, pid: str):
        p = ctx.store.get_profile(pid)
        if p is None:
            return redirect("/profiles", err="Профиль не найден")
        return render(request, "profile_edit.html", p=p, is_new=False, nav="profiles")

    @app.post("/profiles/save")
    async def profile_save(request: Request):
        f = await request.form()
        full_name = (f.get("full_name") or "").strip()
        if not full_name:
            return redirect("/profiles/new", err="Впиши ФИО полностью")
        pid = (f.get("id") or "").strip() or slug(full_name)
        enrollments = []
        for sid, subj in all_subjects().items():
            if f.get(f"enr_{sid}_on"):
                v = (f.get(f"enr_{sid}_variant") or "").strip()
                w = (f.get(f"enr_{sid}_workplace") or "").strip()
                enrollments.append(Enrollment(
                    subject_id=sid, title_subject=(f.get(f"enr_{sid}_title") or subj.name).strip(),
                    teacher=(f.get(f"enr_{sid}_teacher") or "").strip(),
                    academic_year=(f.get(f"enr_{sid}_year") or "").strip(),
                    variant=int(v) if v.isdigit() else subj.default_variant,
                    workplace=int(w) if w.isdigit() else None,
                    header_style="short" if f.get(f"enr_{sid}_style") == "short" else "long"))
        old = await asyncio.to_thread(ctx.store.get_profile, pid)
        base = old.model_dump() if old else Profile(id=pid, full_name=full_name, title_name="").model_dump()
        for key in ("full_name", "title_name", "gender", "group", "specialty_code", "city", "doc_author",
                    "output_root"):
            if key in f:
                base[key] = (f.get(key) or "").strip()
        for key, n in (("college_lines", 3), ("specialty_lines", 3)):
            vals = [f.get(f"{key}_{i}") for i in range(n)]
            if any(v is not None for v in vals):
                base[key] = [v or "" for v in vals]
        base.update(id=pid, enrollments=[e.model_dump() for e in enrollments])
        if not base.get("title_name"):
            parts = full_name.split()
            base["title_name"] = (f"{parts[1][0]}.{parts[2][0]}. {parts[0]}" if len(parts) >= 3 else full_name)
        if not base.get("doc_author"):
            parts = full_name.split()
            base["doc_author"] = " ".join(parts[:2])
        await asyncio.to_thread(ctx.store.save_profile, Profile.model_validate(base))
        return redirect(f"/?profile={_q(pid)}", msg="Профиль сохранён")

    @app.post("/profiles/{pid}/delete")
    def profile_delete(pid: str):
        ctx.store.delete_profile(pid)
        return redirect("/profiles", msg="Профиль удалён")

    @app.post("/subjects/add")
    def subject_add(name: str = Form(...), short_name: str = Form(...), kind: str = Form("theory"),
                    header_style: str = Form("long"), variant: int = Form(14)):
        sid = slug(short_name)
        ctx.add_subject(Subject(id=sid, name=name.strip(), short_name=short_name.strip(),
                                   kind=kind if kind in ("theory", "code", "1c", "network") else "theory",
                                   default_header_style="short" if header_style == "short" else "long",
                                   default_variant=variant))
        return redirect("/profiles", msg=f"Предмет «{name.strip()}» добавлен — отметь его в профиле")

    # новая лаба
    @app.get("/labs/new", response_class=HTMLResponse)
    def lab_new_page(request: Request, profile: str | None = None, subject: str | None = None):
        profiles = ctx.store.list_profiles()
        if not profiles:
            return redirect("/profiles/new", err="Сначала создай профиль")
        return render(request, "lab_new.html", profiles=profiles, profile_id=profile or profiles[0].id,
                      subject_id=subject, nav="home")

    @app.post("/labs/new")
    async def lab_new(profile_id: str = Form(...), subject_id: str = Form(...), number: str = Form(""),
                      text: str = Form(""), files: list[UploadFile] | None = File(None)):
        data = []
        for uf in files or []:
            if uf.filename:
                data.append((uf.filename, await uf.read()))
        if not data and not text.strip():
            return redirect(f"/labs/new?profile={_q(profile_id)}&subject={_q(subject_id)}",
                            err="Добавь файл методички или вставь её текст")
        try:
            st = await asyncio.to_thread(ctx.pipeline.create_lab, profile_id, subject_id, data,
                                         number=number.strip() or None, pasted_text=text)
        except PipelineError as e:
            return redirect(f"/labs/new?profile={_q(profile_id)}&subject={_q(subject_id)}", err=str(e))
        key = ctx.pipeline.key(st)
        result = {}

        def job():
            result["st"] = ctx.pipeline.parse(st, date=today())

        ctx.run(key, job)
        final = result.get("st", st)
        return redirect(lab_url(profile_id, subject_id, final.number))

    # лаба
    def load(p: str, s: str, n: str):
        st = ctx.store.load_lab(p, s, n)
        if st is None:
            raise CloudError("Лаба не найдена")
        return st

    @app.get("/labs/{p}/{s}/{n}", response_class=HTMLResponse)
    def lab_page(request: Request, p: str, s: str, n: str, tab: str | None = None):
        st = ctx.store.load_lab(p, s, n)
        if st is None:
            moved = ctx.pipeline.renamed.get(f"{p}/{s}/{n}")
            if moved:
                return redirect(lab_url(p, s, moved))
            raise CloudError("Лаба не найдена")
        profile = ctx.store.get_profile(p)
        key = ctx.pipeline.key(st)
        busy = key in ctx.running
        events = ctx.bus.history(key)
        journal = [e for e in events if e.kind not in ("idle", "moved")] or ctx.pipeline.journal(st)
        errors = [f for f in st.findings if f.severity == "error" and not is_accepted(f, st.accepted_rules)]
        numbers = {}
        if st.report:
            for b in numbered_blocks(st.report):
                numbers[(st.report.sections[b.section_index].n, b.block_index + 1)] = b.n
        slot_items = [(sec_n, blk, numbers.get((sec_n, blk), "?"), fig) for sec_n, blk, fig in ctx.pipeline.slots(st)]
        if tab not in ("report", "spec", "journal"):
            tab = "journal" if busy else ("spec" if st.status in ("parsed", "confirmed") else "report")
        current = next((e.step for e in reversed(events) if e.step), None) if busy else None
        order = [k for k, _ in STEPS]
        done_upto = {"draft": 0, "parsed": 1, "confirmed": 1, "executing": 1, "needs_manual": 5, "rendered": 5,
                     "final": 5, "failed": 0}[st.status]
        if st.status == "failed":
            done_upto = 1 if st.spec else 0
            if st.report:
                done_upto = 3
        step_state = {}
        for i, k in enumerate(order, start=1):
            if busy and k == current:
                step_state[k] = "now"
            elif i <= done_upto and not (busy and current and order.index(current) < i - 1):
                step_state[k] = "done"
            elif st.status == "failed" and i == done_upto + 1:
                step_state[k] = "fail"
            else:
                step_state[k] = ""
        conclusion_res = goal_to_conclusion(st.spec.goal, profile.gender) if st.spec and profile else None
        conclusion = (st.spec.conclusion if st.spec and st.spec.conclusion else
                      (conclusion_res.text if conclusion_res else ""))
        conclusion_note = conclusion_res.note if conclusion_res and conclusion_res.needs_confirm and not (
            st.spec and st.spec.conclusion) else None
        preview_here = bool(st.pages) and all(Path(x).exists() for x in st.pages)
        docx_here = bool(st.docx_path) and Path(st.docx_path).exists()
        return render(request, "lab.html", st=st, profile=profile, subject=get_subject(s), busy=busy,
                      preview_here=preview_here, docx_here=docx_here,
                      events=events, journal=journal, slot_items=slot_items, errors=errors, tab=tab, steps=STEPS,
                      step_state=step_state, conclusion=conclusion, conclusion_note=conclusion_note,
                      base_url=lab_url(p, s, n), last_seq=max([e.seq for e in events] + [0]), nav="home")

    @app.post("/labs/{p}/{s}/{n}/confirm")
    async def lab_confirm(request: Request, p: str, s: str, n: str):
        if busy_lab(p, s, n):
            return redirect(lab_url(p, s, n), err=BUSY)
        st = await asyncio.to_thread(load, p, s, n)
        f = await request.form()
        tasks_lines = [ln.strip() for ln in (f.get("tasks") or "").splitlines() if ln.strip()]
        extra_from = (f.get("extra_from") or "").strip()
        extra_n = int(extra_from) if extra_from.isdigit() else None
        tasks = [Task(n=i, text=t, kind="extra" if extra_n and i >= extra_n else "main")
                 for i, t in enumerate(tasks_lines, start=1)]
        spec = (st.spec or LabSpec(subject_id=s, number=n, date=today(), topic="", goal="")).model_copy(update={
            "topic": (f.get("topic") or "").strip(), "goal": (f.get("goal") or "").strip(),
            "equipment": (f.get("equipment") or "").strip() or None, "assignment": (f.get("assignment") or "").strip(),
            "date": (f.get("date") or today()).strip(), "tasks": tasks,
            "variant_text": (f.get("variant_text") or "").strip() or None,
            "questions": [ln.strip() for ln in (f.get("questions") or "").splitlines() if ln.strip()]})
        profile = ctx.store.get_profile(p)
        own = " ".join((f.get("conclusion") or "").split())
        formula = goal_to_conclusion(spec.goal, profile.gender if profile else "m").text
        if own and not own.endswith("."):
            own += "."
        spec.conclusion = own if own and own != formula else None
        await asyncio.to_thread(ctx.pipeline.confirm, st, spec)
        return redirect(lab_url(p, s, n), msg="Разбор сохранён")

    @app.post("/labs/{p}/{s}/{n}/execute")
    def lab_execute(p: str, s: str, n: str):
        st = load(p, s, n)
        if st.status in ("parsed",):
            st = ctx.pipeline.confirm(st, None)
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.execute, st)
        return redirect(lab_url(p, s, n))

    @app.post("/labs/{p}/{s}/{n}/reparse")
    def lab_reparse(p: str, s: str, n: str):
        st = load(p, s, n)
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.parse, st, st.spec.date if st.spec else today())
        return redirect(lab_url(p, s, n))

    @app.post("/labs/{p}/{s}/{n}/redo/{t}")
    def lab_redo(p: str, s: str, n: str, t: int):
        st = load(p, s, n)
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.redo_task, st, t)
        return redirect(lab_url(p, s, n))

    @app.post("/labs/{p}/{s}/{n}/chat")
    def lab_chat(p: str, s: str, n: str, message: str = Form(""), remember: str = Form("")):
        if busy_lab(p, s, n):
            return redirect(lab_url(p, s, n), err=BUSY)
        st = load(p, s, n)
        if not message.strip():
            return redirect(lab_url(p, s, n), err="Напиши, что поменять в отчёте")
        if not st.report:
            return redirect(lab_url(p, s, n), err="Чат правок работает после выполнения лабы")
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.chat, st, message, remember == "1")
        return redirect(lab_url(p, s, n) + "?tab=report")

    @app.post("/labs/{p}/{s}/{n}/chat/revert/{vid}")
    def lab_chat_revert(p: str, s: str, n: str, vid: int):
        if busy_lab(p, s, n):
            return redirect(lab_url(p, s, n), err=BUSY)
        st = load(p, s, n)
        if not any(v.id == vid for v in st.versions):
            return redirect(lab_url(p, s, n), err="Этой версии уже нет: хранятся только 10 последних правок")
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.revert, st, vid)
        return redirect(lab_url(p, s, n) + "?tab=report")

    @app.post("/labs/{p}/{s}/{n}/rerender")
    def lab_rerender(p: str, s: str, n: str):
        st = load(p, s, n)
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.render, st)
        return redirect(lab_url(p, s, n))

    @app.post("/labs/{p}/{s}/{n}/screenshot/{sec}/{blk}")
    async def lab_screenshot(request: Request, p: str, s: str, n: str, sec: int, blk: int):
        if busy_lab(p, s, n):
            return JSONResponse({"error": BUSY}, status_code=409)
        st = await asyncio.to_thread(load, p, s, n)
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("multipart/"):
            form = await request.form()
            up = form.get("file")
            data = await up.read() if up else b""
            name = getattr(up, "filename", "") or ""
        else:
            data = await request.body()
            name = ""
        if not data:
            return JSONResponse({"error": "Пустая картинка"}, status_code=400)
        suffix = ".jpg" if (name.lower().endswith((".jpg", ".jpeg")) or "jpeg" in ctype) else ".png"
        if not any(sn == sec and bi == blk for sn, bi, _ in ctx.pipeline.slots(st)):
            return JSONResponse({"error": "Этого места для скриншота уже нет — обнови страницу"}, status_code=400)
        key = ctx.pipeline.key(st)
        # вставка пересобирает документ (Word или LibreOffice) — это в фоне, сервер не замирает
        ctx.run(key, ctx.pipeline.attach_screenshot, st, sec, blk, data, suffix)
        if key in ctx.running:
            return {"status": "working", "error": None}
        st = await asyncio.to_thread(load, p, s, n)
        return {"status": st.status, "error": st.error}

    @app.post("/labs/{p}/{s}/{n}/accept")
    def lab_accept(p: str, s: str, n: str, rule: str = Form(...)):
        if busy_lab(p, s, n):
            return redirect(lab_url(p, s, n), err=BUSY)
        st = load(p, s, n)
        st.accepted_rules = sorted(set(st.accepted_rules) | {rule})
        ctx.store.save_lab(st)
        return redirect(lab_url(p, s, n) + "?tab=report", msg="Замечание принято")

    @app.post("/labs/{p}/{s}/{n}/final")
    def lab_final(p: str, s: str, n: str):
        key = f"{p}/{s}/{n}"
        if not ctx.claim(key):
            return redirect(lab_url(p, s, n), err=BUSY)
        try:
            ctx.pipeline.mark_final(load(p, s, n))
        except PipelineError as e:
            return redirect(lab_url(p, s, n), err=str(e))
        finally:
            ctx.release(key)
        return redirect(lab_url(p, s, n), msg="Лаба сдана: файл лежит в папке сдачи")

    @app.post("/labs/{p}/{s}/{n}/delete")
    def lab_delete(p: str, s: str, n: str):
        if busy_lab(p, s, n):
            return redirect(lab_url(p, s, n), err=BUSY)
        ctx.store.delete_lab(p, s, n)
        return redirect(f"/?profile={_q(p)}&subject={_q(s)}", msg="Лаба удалена")

    @app.get("/labs/{p}/{s}/{n}/docx")
    def lab_docx(p: str, s: str, n: str):
        st = load(p, s, n)
        if not st.docx_path or not Path(st.docx_path).exists():
            raise CloudError("Документ ещё не свёрстан")
        subj = get_subject(s)
        return FileResponse(st.docx_path, filename=f"ЛР{n}_{subj.short_name}.docx",
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    @app.post("/labs/{p}/{s}/{n}/open")
    def lab_open(p: str, s: str, n: str, what: str = Form("docx")):
        st = load(p, s, n)
        target = st.docx_path if what == "docx" else str(ctx.store.lab_dir(p, s, n))
        if target and Path(target).exists():
            _open_local(target)
        return redirect(lab_url(p, s, n))

    @app.get("/labs/{p}/{s}/{n}/page/{i}")
    def lab_page_png(p: str, s: str, n: str, i: int):
        st = load(p, s, n)
        if not (1 <= i <= len(st.pages)) or not Path(st.pages[i - 1]).exists():
            return JSONResponse({"error": "нет страницы"}, status_code=404)
        return FileResponse(st.pages[i - 1], media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/labs/{p}/{s}/{n}/state")
    def lab_state(p: str, s: str, n: str):
        st = load(p, s, n)
        key = ctx.pipeline.key(st)
        return {"status": st.status, "busy": key in ctx.running, "pages": len(st.pages)}

    @app.get("/labs/{p}/{s}/{n}/events")
    async def lab_events(request: Request, p: str, s: str, n: str, after: int = 0):
        key = f"{p}/{s}/{n}"

        async def gen():
            last = after
            for _ in range(240):
                if await request.is_disconnected():
                    break
                evs = await asyncio.to_thread(ctx.bus.wait, key, last, 10.0)
                for e in evs:
                    last = e.seq
                    payload = {"seq": e.seq, "text": e.text, "kind": e.kind, "step": e.step,
                               "busy": key in ctx.running}
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if not evs:
                    yield ": ping\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    # отчёт за семестр
    @app.get("/semester", response_class=HTMLResponse)
    def semester_page(request: Request, profile: str | None = None, subject: str | None = None):
        profiles = ctx.store.list_profiles()
        current = next((p for p in profiles if p.id == profile), profiles[0] if profiles else None)
        labs = []
        if current:
            subject = subject or (current.enrollments[0].subject_id if current.enrollments else None)
            if subject:
                labs = [l for l in ctx.store.list_labs(current.id, subject) if l.report]
        return render(request, "semester.html", profiles=profiles, current=current, subject_id=subject, labs=labs,
                      nav="semester")

    @app.post("/semester")
    async def semester_build(request: Request):
        f = await request.form()
        pid, sid = f.get("profile_id"), f.get("subject_id")
        numbers = f.getlist("numbers")
        out_dir = ctx.store.local.home if hasattr(ctx.store, "local") else ctx.store.home
        out = Path(out_dir) / "semester" / f"{pid}_{sid}.docx"
        try:
            path, rep = await asyncio.to_thread(ctx.pipeline.build_semester, pid, sid, numbers, out)
        except PipelineError as e:
            return redirect(f"/semester?profile={_q(pid)}&subject={_q(sid)}", err=str(e))
        subj = get_subject(sid)
        profile = await asyncio.to_thread(ctx.store.get_profile, pid)
        name = f"{subj.short_name}_{(profile.full_name.split() or ['отчёт'])[0]}_семестр.docx"
        return FileResponse(path, filename=name,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    # правила
    @app.get("/rules", response_class=HTMLResponse)
    def rules_page(request: Request, file: str | None = None):
        files = ctx.rules().files()
        current = next((x for x in files if x.path == file), files[0] if files else None)
        return render(request, "rules.html", files=files, current=current, nav="rules")

    @app.post("/rules/save")
    def rules_save(path: str = Form(...), text: str = Form(...)):
        try:
            ctx.rules().save_override(path, text)
        except KeyError:
            return redirect("/rules", err="Такого файла правил нет")
        ctx.reset()
        return redirect(f"/rules?file={_q(path)}", msg="Правило сохранено")

    @app.post("/rules/reset")
    def rules_reset(path: str = Form(...)):
        try:
            ctx.rules().reset_override(path)
        except KeyError:
            return redirect("/rules", err="Такого файла правил нет")
        ctx.reset()
        return redirect(f"/rules?file={_q(path)}", msg="Возвращён исходный текст")

    # настройки
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        my_balance, balance_state = None, "offline"
        if ctx.cloud and ctx.auth.session:
            try:
                my_balance, balance_state = ctx.store.my_balance()
            except CloudError:
                pass
        return render(request, "settings.html", s=ctx.settings, presets=API_PRESETS, has_key=bool(get_api_key()),
                      word=word_com.available(), soffice=libreoffice.find_soffice(ctx.settings.soffice_path),
                      nav="settings", my_balance=my_balance, balance_state=balance_state)

    @app.post("/settings")
    async def settings_save(request: Request):
        f = await request.form()
        data = ctx.settings.model_dump()
        for key in ("mode", "base_url", "model", "soffice_path", "tesseract_path", "browser_channel"):
            if key in f:
                data[key] = (f.get(key) or "").strip() or None
        data["mode"] = data.get("mode") or "cloud"
        data["base_url"] = data.get("base_url") or Settings().base_url
        data["model"] = data.get("model") or Settings().model
        ctx.settings = Settings.model_validate(data)
        save_settings(ctx.settings)
        key = (f.get("api_key") or "").strip()
        if key and not set_api_key(key):
            os.environ["FORLABS_API_KEY"] = key
        ctx.reset()
        return redirect("/settings", msg="Настройки сохранены")

    @app.post("/settings/check")
    def settings_check():
        try:
            model = ctx.llm().check()
        except (LLMError, CloudError) as e:
            return redirect("/settings", err=getattr(e, "message_ru", str(e)))
        return redirect("/settings", msg=f"Связь есть, модель отвечает: {model}")

    register_admin(app, ctx, render, redirect)

    return app


def _open_local(target: str) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(target)  # noqa: S606 — открыть файл/папку пользователя
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception:
        pass
