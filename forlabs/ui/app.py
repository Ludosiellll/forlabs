"""Локальный веб-интерфейс ForLabs (FastAPI + шаблоны)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from forlabs.cloud.auth import CloudError
from forlabs.core.fs import os_error_text
from forlabs.core.llm import LLMError
from forlabs.core.models import (
    Enrollment,
    LabSpec,
    Profile,
    Task,
    finding_key,
    is_accepted,
)
from forlabs.core.settings import (
    API_PRESETS,
    MODEL_CHOICES,
    Settings,
    get_api_key,
    save_settings,
    set_api_key,
)
from forlabs.core.store import safe_file_name, safe_name
from forlabs.core.subjects import Subject, all_subjects, get_subject
from forlabs.pipeline.images import MAX_IMAGES, normalize_screenshot, prepare_image
from forlabs.pipeline.materials import ACCEPT as MATERIALS_ACCEPT, MATERIALS_MAX, PAGE_CHARS, pages
from forlabs.pipeline.labnum import shown_names, split_by_lab
from forlabs.pipeline.memory import parse_numbers
from forlabs import version as _version
from forlabs.pipeline.run import (
    PipelineError,
    docx_fresh,
    error_text,
    is_draft,
    lab_complete,
    output_root,
    running_elsewhere,
    today,
)
from forlabs.render import libreoffice, word_com
from forlabs.report.derive import numbered_blocks
from forlabs.report.morph import goal_to_conclusion
from forlabs.ui.admin_routes import (
    access_text,
    ago,
    date_ru,
    labs_estimate,
    register_admin,
)
from forlabs.ui.charts import fmt_tokens
from forlabs.ui.context import AppContext, slug

HERE = Path(__file__).parent
_NOTE_NAME = re.compile(r"(wish|chat_[0-9a-f]{8})_\d{1,2}\.jpg")
LOCAL_HOSTS = ["127.0.0.1", "localhost"]
STATUS_RU = {"draft": "Черновик", "parsed": "Проверь разбор", "confirmed": "Готова к выполнению",
             "executing": "Выполняется", "needs_manual": "Нужны скриншоты", "rendered": "Свёрстана",
             "final": "Сдана", "failed": "Ошибка"}
STEPS = [("parse", "Разбор методички"), ("write", "Текст отчёта"), ("check", "Проверка"), ("render", "Вёрстка"),
         ("finalize", "Доводка и предпросмотр")]


EVENT_WAITERS = __import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=32,
                                                                            thread_name_prefix="events")
MAX_UPLOAD = 50_000_000  # байт на файл методички или фото
ELSEWHERE = ("Эта лаба сейчас выполняется на другом компьютере. Дождись окончания там (или 15 минут, если тот "
             "компьютер выключили) и обнови страницу.")
BUSY_MESSAGE = "Сейчас идёт работа над лабой. Дождись её окончания, потом меняй настройки или выходи."


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


def create_app(ctx: AppContext | None = None, allowed_hosts: list[str] | None = None,
               token: str | None = None) -> FastAPI:
    """token — ключ этого запуска: без него (cookie или заголовок) сервер не отвечает. Порт 127.0.0.1 общий для
    всех пользователей Windows — без ключа второй пользователь компьютера открыл бы чужие лабы."""
    ctx = ctx or AppContext()
    app = FastAPI(title="ForLabs", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    tpl = Jinja2Templates(directory=HERE / "templates")
    from forlabs.pipeline.memory import teacher_key

    tpl.env.filters["teacher_key"] = teacher_key
    from forlabs.runner.languages import TOOL_TITLES

    tpl.env.filters["tool_title"] = lambda key: TOOL_TITLES.get(key, key)
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
            m = base["member"]
            if m is not None and m.on_trial and m.approved:
                base["trial"] = ctx.store.trial_usage(m)
            try:
                from forlabs.cloud.support import Support

                base["support_unread"] = Support(ctx.store).unread()
            except Exception:  # без связи или без таблицы поддержки — без значка
                base["support_unread"] = 0
        base["show_tour"] = name == "home.html" and not ctx.settings.tour_done
        m = base.get("member")
        base["show_rules"] = not ctx.cloud or bool(m and m.role == "admin" and m.approved)
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

    BUSY = "По этой лабе сейчас идёт работа, дождись её окончания."

    def busy_lab(p: str, s: str, n: str) -> bool:
        return f"{p}/{s}/{n}" in ctx.running

    # Порядок: последний добавленный работает первым — имя хоста → ключ запуска → чужой сайт → вход.
    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        open_paths = ("/login", "/register", "/static", "/health", "/focus")
        if ctx.cloud and not ctx.auth.session and not path.startswith(open_paths):
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    @app.middleware("http")
    async def owner_only(request: Request, call_next):
        if token is None or request.url.path == "/health" or request.url.path.startswith("/static/"):
            return await call_next(request)
        import secrets
        from urllib.parse import urlencode

        def same(v: str | None) -> bool:
            return bool(v) and secrets.compare_digest(v.encode(), token.encode())

        if same(request.query_params.get("t")):  # адрес окна с ключом → cookie, дальше адрес без ключа
            rest = [(k, v) for k, v in request.query_params.multi_items() if k != "t"]
            resp = RedirectResponse(request.url.path + ("?" + urlencode(rest) if rest else ""), status_code=303)
            resp.set_cookie("forlabs_token", token, httponly=True, samesite="strict")
            return resp
        if same(request.cookies.get("forlabs_token")) or same(request.headers.get("x-forlabs-token")):
            return await call_next(request)
        return HTMLResponse("<p>Эта копия ForLabs запущена другим пользователем компьютера или ссылка устарела. "
                            "Открой ForLabs своим ярлыком.</p>", status_code=403)

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_origin(request):
            return HTMLResponse("Запрос пришёл с другого сайта и отклонён.", status_code=403)
        response = await call_next(request)
        # чужой сайт не встроит ForLabs во фрейм, чтобы подсунуть клик по кнопке (кликджекинг)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # Последним добавлен — значит, срабатывает первым: чужое имя хоста (DNS rebinding) отсекается сразу.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or LOCAL_HOSTS)

    # обновление из приложения
    @app.get("/update/status")
    def update_status():
        return ctx.updater.status()

    @app.post("/update/start")
    def update_start():
        if ctx.synchronous:
            ctx.updater.download()
            return {"ok": ctx.updater.state == "ready", **ctx.updater.status()}
        return {"ok": ctx.updater.start_download(), **ctx.updater.status()}

    @app.post("/update/later")
    def update_later():
        ctx.updater.dismissed = True  # до следующего запуска ForLabs не напоминать
        return {"ok": True}

    @app.post("/update/install")
    def update_install():
        if ctx.updater.state != "ready":
            return JSONResponse({"ok": False, "error": "Обновление ещё не скачано."}, status_code=409)
        if ctx.busy():
            return JSONResponse({"ok": False, "error": "Сейчас выполняется лаба. Обновлю, когда она закончится."},
                                status_code=409)
        try:
            ctx.updater.install(os.getpid(), spawn=ctx.spawn_installer)
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"Установщик не запустился: {e}"}, status_code=500)
        if callable(ctx.quit):  # окно закрывается — установщик дождётся, поставит новую версию и откроет её
            if ctx.synchronous:
                ctx.quit()
            else:
                threading.Timer(1.5, ctx.quit).start()
        return {"ok": True}

    @app.post("/focus")
    def focus():
        """Второй запуск ForLabs просит вывести окно этого вперёд (ключ — в заголовке)."""
        f = getattr(ctx, "focus", None)
        if callable(f):
            f()
        return {"ok": True}

    @app.get("/health")
    def health():
        from forlabs.version import __version__

        return {"app": "forlabs", "version": __version__}

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        import logging

        logging.getLogger("forlabs").error("Ошибка на %s %s", request.method, request.url.path, exc_info=exc)
        text = os_error_text(exc) if isinstance(exc, OSError) else (str(exc) or type(exc).__name__)
        response = render(request, "error.html", message="Что-то пошло не так. Подробности — в журнале forlabs.log "
                                                         "в папке данных.", detail=text[:300])
        response.status_code = 500
        return response

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
    def logout(wipe: str = Form("")):
        if ctx.running:
            return redirect("/", err=BUSY_MESSAGE)
        if wipe and ctx.cloud and hasattr(ctx.store, "wipe_local"):
            try:
                ctx.store.wipe_local()  # общий компьютер: копию своих лаб не оставлять
            except CloudError as e:
                return redirect("/", err=e.message_ru)
        ctx.auth.sign_out()
        ctx.reset()
        return redirect("/login")

    # главная
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, profile: str | None = None, subject: str | None = None):
        member = None
        if ctx.cloud:
            try:
                member = ctx.store.member()
            except CloudError as e:
                if not e.offline:
                    raise
                member = None  # без связи: главная из копии на этом компьютере
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
        labs = ctx.store.list_lab_summaries(current.id, subject_id)
        counts = {}
        for sid in enrolled:
            try:
                counts[sid] = len(ctx.store.list_lab_summaries(current.id, sid)) if sid != subject_id else len(labs)
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

    def abbreviation(name: str) -> str:
        """«Технология разработки программного обеспечения» → «ТРПО», «Объектно-ориентированное …» → «ООП»."""
        words = [w for w in re.split(r"[\s\-–]+", name) if len(w) >= 3 or w.isupper()]
        short = "".join(w[0].upper() for w in words)
        return short if len(short) >= 2 else name[:20]

    def academic_year() -> str:
        from datetime import date

        d = date.today()
        y = d.year if d.month >= 8 else d.year - 1
        return f"{y}/{y + 1}"

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
        new_name = (f.get("new_subject_name") or "").strip()
        if new_name:  # нужного предмета нет в списке — свой, сразу отмеченный у этого студента
            v = (f.get("new_subject_variant") or "").strip()
            style = "short" if f.get("new_subject_style") == "short" else "long"
            subj = await asyncio.to_thread(create_subject, new_name, f.get("new_subject_short") or "",
                                           f.get("new_subject_kind") or "theory", style, int(v) if v.isdigit() else 1)
            if not any(e.subject_id == subj.id for e in enrollments):
                enrollments.append(Enrollment(
                    subject_id=subj.id, title_subject=subj.name,
                    teacher=(f.get("new_subject_teacher") or "").strip(), academic_year=academic_year(),
                    variant=int(v) if v.isdigit() else subj.default_variant, header_style=style))
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

    def create_subject(name: str, short_name: str = "", kind: str = "theory", header_style: str = "long",
                       variant: int = 14) -> Subject:
        """Свой предмет. Такой же по названию уже есть — он и возвращается (без дублей)."""
        name = " ".join(name.split())
        same = next((s for s in all_subjects().values() if s.name.lower() == name.lower()), None)
        if same is not None:
            return same
        short_name = " ".join((short_name or "").split()) or abbreviation(name)
        sid, k = slug(short_name), 2
        base = sid
        while sid in all_subjects():  # не заменять предмет с тем же сокращением
            sid, k = f"{base}-{k}", k + 1
        subj = Subject(id=sid, name=name, short_name=short_name,
                       kind=kind if kind in ("theory", "code", "1c", "network") else "theory",
                       default_header_style="short" if header_style == "short" else "long",
                       default_variant=max(1, variant))
        ctx.add_subject(subj)
        return subj

    @app.post("/subjects/add")
    def subject_add(name: str = Form(...), short_name: str = Form(""), kind: str = Form("theory"),
                    header_style: str = Form("long"), variant: int = Form(14)):
        if not name.strip():
            return redirect("/profiles", err="Впиши название предмета")
        subj = create_subject(name, short_name, kind, header_style, variant)
        return redirect("/profiles", msg=f"Предмет «{subj.name}» добавлен, отметь его в профиле")

    # новая лаба
    @app.get("/labs/new", response_class=HTMLResponse)
    def lab_new_page(request: Request, profile: str | None = None, subject: str | None = None):
        profiles = ctx.store.list_profiles()
        if not profiles:
            return redirect("/profiles/new", err="Сначала создай профиль")
        return render(request, "lab_new.html", profiles=profiles, profile_id=profile or profiles[0].id,
                      subject_id=subject, nav="home", form_token=uuid.uuid4().hex,
                      materials_accept=MATERIALS_ACCEPT, materials_pages=MATERIALS_MAX // PAGE_CHARS)

    @app.post("/labs/new")
    async def lab_new(profile_id: str = Form(...), subject_id: str = Form(...), number: str = Form(""),
                      text: str = Form(""), wishes: str = Form(""), files: list[UploadFile] | None = File(None),
                      wish_images: list[UploadFile] | None = File(None), form_token: str = Form(""),
                      linked: str = Form(""), linked_report: UploadFile | None = File(None),
                      materials: list[UploadFile] | None = File(None)):
        if form_token:  # двойной клик или повторная отправка формы — одна лаба
            with ctx._lock:
                done = ctx.form_tokens.get(form_token)
                if done is None:
                    ctx.form_tokens[form_token] = "pending"
            if done == "pending":
                return redirect("/", msg="Лаба уже создаётся и появится в списке")
            if done:
                return RedirectResponse(done, status_code=303)
        data = []
        back = f"/labs/new?profile={_q(profile_id)}&subject={_q(subject_id)}"
        for uf in [*(files or []), *(wish_images or []), *(materials or []), *([linked_report] if linked_report else [])]:
            if uf.filename and (uf.size or 0) > MAX_UPLOAD:
                return redirect(back, err=f"Файл «{uf.filename}» больше {MAX_UPLOAD // 1_000_000} МБ. Оставь только "
                                          "страницы своей лабы или сожми его.")
        for uf in files or []:
            if uf.filename:
                data.append((uf.filename, await uf.read()))
        if not data and not text.strip():
            return redirect(f"/labs/new?profile={_q(profile_id)}&subject={_q(subject_id)}",
                            err="Добавь файл методички или вставь её текст")
        photos = [(uf.filename, await uf.read()) for uf in (wish_images or []) if uf.filename]
        extra = [(uf.filename, await uf.read()) for uf in (materials or []) if uf.filename]
        report = (linked_report.filename, await linked_report.read()) if linked_report and linked_report.filename else None
        numbers = parse_numbers(linked)

        async def start(st, hint: str | None = None):
            """Файлы к лабе, затем разбор в фоне; связи — после разбора. → (лаба, замечания)."""
            notes: list[str] = []
            if extra:  # лаба уже создана: сбой с файлами — замечание, а не брошенный черновик без разбора
                try:
                    st, problems = await asyncio.to_thread(ctx.pipeline.add_materials, st, extra)
                    notes += problems
                except Exception as e:
                    notes.append(f"Файлы к лабе не добавились ({getattr(e, 'message_ru', None) or e}). Добавь их на "
                                 "странице лабы, слева.")
            key = ctx.pipeline.key(st)
            result = {}

            def job():
                done = result["st"] = ctx.pipeline.parse(st, date=today(), number=hint)
                if not (numbers or report):
                    return
                # связи — после разбора: известны ссылки методички и тема, по которой модель ищет лабу без заголовка
                new_key = ctx.pipeline.key(done)
                claimed = new_key != key and ctx.claim(new_key)  # лаба переехала под номер из методички — тоже занята
                try:
                    result["st"], _ = ctx.pipeline.set_links(done, numbers, report)
                except Exception as e:
                    ctx.bus.publish(new_key, f"Связанные лабы не сохранены ({getattr(e, 'message_ru', None) or e}). "
                                             "Укажи их на странице лабы, слева.", "warn")
                finally:
                    if claimed:
                        ctx.release(new_key)

            ctx.run(key, job)
            return result.get("st", st), notes

        # методички разных лаб («ЛР19…», «ЛР20…») — отдельные лабы; номер, вписанный в форму, — одна лаба на всё
        groups = await asyncio.to_thread(split_by_lab, data, text) if not number.strip() and len(data) >= 2 else []
        if groups:
            made: list[str] = []
            problems: list[str] = []
            failed: list[Exception] = []
            for n, part in groups:
                try:  # номер занят — черновик: после разбора станет «N-2», как обычно
                    taken = await asyncio.to_thread(ctx.pipeline._taken, profile_id, subject_id, n)
                    st = await asyncio.to_thread(ctx.pipeline.create_lab, profile_id, subject_id, part,
                                                 number=None if taken else n, wishes=wishes, wish_images=photos)
                except Exception as e:  # одна не создалась — остальные создаются
                    failed.append(e)
                    problems.append(f"ЛР {n} не создана: {error_text(e)}")
                    continue
                final, notes = await start(st, hint=n)
                made.append(n if is_draft(final.number) else final.number)
                problems += [x for x in notes if x not in problems]
            if not made:
                if form_token:
                    ctx.form_tokens.pop(form_token, None)
                if isinstance(failed[0], PipelineError):
                    return redirect(back, err=str(failed[0]))
                raise failed[0]
            url = (f"/?profile={_q(profile_id)}&subject={_q(subject_id)}&msg="
                   + _q(f"Методички разных лаб — создал отдельные: {', '.join('ЛР ' + n for n in made)}. "
                        "Проверь разбор у каждой.")
                   + (f"&err={_q(' '.join(problems))}" if problems else ""))
            if form_token:
                ctx.form_tokens[form_token] = url
            return RedirectResponse(url, status_code=303)

        try:
            st = await asyncio.to_thread(ctx.pipeline.create_lab, profile_id, subject_id, data,
                                         number=number.strip() or None, pasted_text=text, wishes=wishes,
                                         wish_images=photos)
        except Exception as e:  # лаба не создана — форму можно отправить снова
            if form_token:
                ctx.form_tokens.pop(form_token, None)
            if isinstance(e, PipelineError):
                return redirect(back, err=str(e))
            raise
        final, notes = await start(st)
        url = lab_url(profile_id, subject_id, final.number)
        if form_token:
            ctx.form_tokens[form_token] = url
        return redirect(url, err=" ".join(notes) or None)

    # лаба
    def load(p: str, s: str, n: str, strict: bool = True):
        """strict — для действий: без связи с облаком не работать с копией на компьютере (её можно только смотреть),
        иначе после возврата связи она перезапишет лабу, изменённую на другом компьютере."""
        st = ctx.store.load_lab(p, s, n, strict=strict)
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
        try:
            teacher_rules = ctx.memory().teacher_list(p, s)
        except Exception:
            teacher_rules = []
        try:
            subject_theme = ctx.memory().theme(p, s)
            subject_memory = [ln for ln in ctx.memory().read(p, s).splitlines() if ln.strip()]
        except Exception:
            subject_theme, subject_memory = "", []
        fresh = docx_fresh(st, ctx.store.lab_dir(p, s, n))  # документ — от последней удачной вёрстки этой лабы
        preview_here = fresh and bool(st.pages) and all(Path(x).exists() for x in st.pages)
        docx_here = fresh
        last = st.versions[-1] if st.versions else None  # методичку заменили, отчёт ещё не написан заново
        restore_vid = last.id if last and not st.report and last.label == "до замены методички" else None
        return render(request, "lab.html", st=st, profile=profile, subject=get_subject(s), busy=busy,
                      source_names=shown_names(ctx.pipeline.source_names(st)),
                      open_replace=request.query_params.get("replace") == "1", restore_vid=restore_vid,
                      subject_theme=subject_theme, subject_memory=subject_memory,
                      link_notes=[f.message for f in st.findings if f.rule_id.startswith("spec.linked")],
                      auto_refs=[("предыдущую лабу" if r == "previous" else f"ЛР {r}") for r in ctx.pipeline._auto_refs(st)],
                      materials=[(m.name, pages(len(m.text))) for m in st.materials],
                      materials_accept=MATERIALS_ACCEPT, materials_pages=MATERIALS_MAX // PAGE_CHARS,
                      preview_here=preview_here, docx_here=docx_here, teacher_rules=teacher_rules,
                      elsewhere=running_elsewhere(st),
                      complete=lab_complete(st),
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
            "wishes": ((f.get("wishes") or "").strip()[:4000] or None) if "wishes" in f
            else (st.spec.wishes if st.spec else None),
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
    def lab_execute(p: str, s: str, n: str, full: str = Form("")):
        st = load(p, s, n)
        if running_elsewhere(st):
            return redirect(lab_url(p, s, n), err=ELSEWHERE)
        if st.status in ("parsed",):
            st = ctx.pipeline.confirm(st, None)
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.execute, st, full=bool(full))
        return redirect(lab_url(p, s, n))

    @app.post("/labs/{p}/{s}/{n}/reparse")
    def lab_reparse(p: str, s: str, n: str):
        st = load(p, s, n)
        if running_elsewhere(st):
            return redirect(lab_url(p, s, n), err=ELSEWHERE)
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.parse, st, st.spec.date if st.spec else today())
        return redirect(lab_url(p, s, n))

    @app.post("/labs/{p}/{s}/{n}/source")
    async def lab_source(p: str, s: str, n: str, files: list[UploadFile] | None = File(None), text: str = Form("")):
        """Заменить методичку: новые файлы или текст вместо прежних, разбор — заново, в фоне."""
        back = lab_url(p, s, n) + "?tab=spec&replace=1"
        if busy_lab(p, s, n):
            return redirect(back, err=BUSY)
        ups = [uf for uf in files or [] if uf.filename]
        for uf in ups:
            if (uf.size or 0) > MAX_UPLOAD:
                return redirect(back, err=f"Файл «{uf.filename}» больше {MAX_UPLOAD // 1_000_000} МБ — оставь только "
                                          "страницы своей лабы или сожми его.")
        if not ups and not text.strip():
            return redirect(back, err="Добавь файл методички или вставь её текст")
        st = await asyncio.to_thread(load, p, s, n)
        if running_elsewhere(st):
            return redirect(lab_url(p, s, n), err=ELSEWHERE)
        data = [(uf.filename, await uf.read()) for uf in ups]
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.replace_source, st, data, text)
        return redirect(lab_url(p, s, n))

    @app.post("/labs/{p}/{s}/{n}/redo/{t}")
    def lab_redo(p: str, s: str, n: str, t: int):
        st = load(p, s, n)
        if running_elsewhere(st):
            return redirect(lab_url(p, s, n), err=ELSEWHERE)
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.redo_task, st, t)
        return redirect(lab_url(p, s, n))

    @app.post("/labs/{p}/{s}/{n}/chat")
    def lab_chat(p: str, s: str, n: str, message: str = Form(""), remember: str = Form(""),
                 images: list[UploadFile] | None = File(None)):
        if busy_lab(p, s, n):
            return redirect(lab_url(p, s, n), err=BUSY)
        st = load(p, s, n)
        photos = [(uf.filename, uf.file.read()) for uf in (images or []) if uf.filename]
        if not message.strip() and not photos:
            return redirect(lab_url(p, s, n), err="Напиши, что поменять в отчёте, или приложи фото")
        if not st.report:
            return redirect(lab_url(p, s, n), err="Чат правок работает после выполнения лабы")
        if not lab_complete(st):
            return redirect(lab_url(p, s, n), err="Лаба дописана не до конца, сначала нажми «Продолжить»")
        if len(photos) > MAX_IMAGES:
            return redirect(lab_url(p, s, n), err=f"Не больше {MAX_IMAGES} фото за раз")
        try:
            for _, data in photos:
                prepare_image(data)  # проверка сразу, а не в фоне
        except ValueError as e:
            return redirect(lab_url(p, s, n), err=str(e))
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.chat, st, message, remember == "1", photos)
        return redirect(lab_url(p, s, n) + "?tab=report")

    @app.get("/labs/{p}/{s}/{n}/notes/{name}")
    def lab_note_image(p: str, s: str, n: str, name: str):
        if not _NOTE_NAME.fullmatch(name):
            return JSONResponse({"error": "нет такого фото"}, status_code=404)
        st = load(p, s, n)
        rel = f"notes/{name}"
        if rel not in st.wish_images and not any(rel in m.images for m in st.chat):
            return JSONResponse({"error": "нет такого фото"}, status_code=404)
        f = ctx.store.lab_dir(p, s, n) / rel
        if not f.exists() and hasattr(ctx.store, "fetch"):
            try:
                ctx.store.fetch(p, s, n, rel, f)
            except CloudError:
                pass
        if not f.exists():
            return JSONResponse({"error": "нет такого фото"}, status_code=404)
        return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/labs/{p}/{s}/{n}/links")
    async def lab_links(p: str, s: str, n: str, linked: str = Form(""),
                        linked_report: UploadFile | None = File(None)):
        if busy_lab(p, s, n):
            return redirect(lab_url(p, s, n), err=BUSY)
        if linked_report is not None and (linked_report.size or 0) > MAX_UPLOAD:
            return redirect(lab_url(p, s, n), err=f"Отчёт больше {MAX_UPLOAD // 1_000_000} МБ, сожми в нём картинки.")
        st = await asyncio.to_thread(load, p, s, n)
        report = (linked_report.filename, await linked_report.read()) if linked_report and linked_report.filename else None
        # с отчётом может понадобиться модель (лаба без заголовка) — в фоне, итог появится в блоке слева
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.set_links, st, parse_numbers(linked), report)
        if report:
            return redirect(lab_url(p, s, n), msg="Ищу связанные лабы в отчёте, найденные появятся слева")
        return redirect(lab_url(p, s, n), msg="Связанные лабы сохранены" if linked.strip() else "Связанные лабы убраны")

    @app.post("/labs/{p}/{s}/{n}/materials")
    async def lab_materials(p: str, s: str, n: str, materials: list[UploadFile] | None = File(None)):
        if busy_lab(p, s, n):
            return redirect(lab_url(p, s, n), err=BUSY)
        files = [uf for uf in (materials or []) if uf.filename]
        if not files:
            return redirect(lab_url(p, s, n), err="Выбери файл")
        for uf in files:
            if (uf.size or 0) > MAX_UPLOAD:
                return redirect(lab_url(p, s, n), err=f"Файл «{uf.filename}» больше {MAX_UPLOAD // 1_000_000} МБ")
        st = await asyncio.to_thread(load, p, s, n)
        data = [(uf.filename, await uf.read()) for uf in files]
        st, problems = await asyncio.to_thread(ctx.pipeline.add_materials, st, data)
        if problems:
            return redirect(lab_url(p, s, n), err=" ".join(problems))
        return redirect(lab_url(p, s, n), msg="Файлы добавлены, модель прочитает их в каждом задании")

    @app.post("/labs/{p}/{s}/{n}/materials/remove")
    def lab_materials_remove(p: str, s: str, n: str, name: str = Form("")):
        if busy_lab(p, s, n):
            return redirect(lab_url(p, s, n), err=BUSY)
        ctx.pipeline.remove_material(load(p, s, n), name)
        return redirect(lab_url(p, s, n), msg="Файл убран")

    @app.post("/labs/{p}/{s}/{n}/memory/theme")
    def lab_memory_theme(p: str, s: str, n: str, theme: str = Form("")):
        ctx.memory().set_theme(p, s, theme)
        return redirect(lab_url(p, s, n), msg="Тематика предмета сохранена" if theme.strip() else "Тематика убрана")

    @app.post("/labs/{p}/{s}/{n}/teacher/remove/{key}")
    def lab_teacher_remove(p: str, s: str, n: str, key: str):
        ctx.memory().remove_teacher(p, s, key)  # по тексту, а не по номеру: двойной клик не убирает соседнее
        return redirect(lab_url(p, s, n) + "?tab=report", msg="Требование убрано")

    @app.post("/labs/{p}/{s}/{n}/chat/revert/{vid}")
    def lab_chat_revert(p: str, s: str, n: str, vid: int):
        if busy_lab(p, s, n):
            return redirect(lab_url(p, s, n), err=BUSY)
        st = load(p, s, n)
        if not any(v.id == vid for v in st.versions):
            return redirect(lab_url(p, s, n), err="Этой версии уже нет: хранятся только 10 последних правок")
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.revert, st, vid)
        return redirect(lab_url(p, s, n) + "?tab=report")

    @app.post("/labs/{p}/{s}/{n}/chat/profile/{msg_id}")
    def lab_chat_profile(p: str, s: str, n: str, msg_id: str, apply: str = Form("0")):
        if busy_lab(p, s, n):
            return redirect(lab_url(p, s, n), err=BUSY)
        st = load(p, s, n)
        msg = next((m for m in st.chat if m.id == msg_id and m.profile), None)
        if msg is None or msg.profile_state:
            return redirect(lab_url(p, s, n), err="Это предложение уже обработано")
        try:
            if apply == "1":
                ctx.run(ctx.pipeline.key(st), ctx.pipeline.apply_profile_change, st, msg_id, True)
                return redirect(lab_url(p, s, n), msg="Профиль обновлён, документ пересобирается")
            ctx.pipeline.apply_profile_change(st, msg_id, False)
        except PipelineError as e:
            return redirect(lab_url(p, s, n), err=str(e))
        return redirect(lab_url(p, s, n))

    @app.post("/labs/{p}/{s}/{n}/rerender")
    def lab_rerender(p: str, s: str, n: str):
        st = load(p, s, n)
        if running_elsewhere(st):
            return redirect(lab_url(p, s, n), err=ELSEWHERE)
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.render, st)
        return redirect(lab_url(p, s, n))

    @app.post("/labs/{p}/{s}/{n}/screenshot/{sec}/{blk}")
    async def lab_screenshot(request: Request, p: str, s: str, n: str, sec: int, blk: int):
        if busy_lab(p, s, n):
            return JSONResponse({"error": BUSY}, status_code=409)
        try:
            st = await asyncio.to_thread(load, p, s, n)
        except CloudError as e:  # ответ читает app.js: нужна причина в JSON, а не страница ошибки
            return JSONResponse({"error": e.message_ru}, status_code=503 if e.offline else 400)
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
            return JSONResponse({"error": "Этого места для скриншота уже нет, обнови страницу"}, status_code=400)
        try:
            normalize_screenshot(data)  # проверка сразу: ошибка видна у места для снимка, а не в журнале
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
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
        st = load(p, s, n, strict=False)
        if not docx_fresh(st, ctx.store.lab_dir(p, s, n)):
            return redirect(lab_url(p, s, n), err="Документ устарел или ещё не свёрстан. Нажми «Пересобрать документ».")
        subj = get_subject(s)
        return FileResponse(st.docx_path, filename=f"ЛР{n}_{subj.short_name}.docx",
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    @app.post("/labs/{p}/{s}/{n}/open")
    def lab_open(p: str, s: str, n: str, what: str = Form("docx")):
        st = load(p, s, n, strict=False)
        if what == "docx" and not docx_fresh(st, ctx.store.lab_dir(p, s, n)):
            return redirect(lab_url(p, s, n), err="Документ устарел или ещё не свёрстан. Нажми «Пересобрать документ».")
        target = st.docx_path if what == "docx" else str(ctx.store.lab_dir(p, s, n))
        if target and Path(target).exists():
            _open_local(target)
        return redirect(lab_url(p, s, n))

    @app.post("/labs/{p}/{s}/{n}/project/retry")
    def lab_project_retry(p: str, s: str, n: str):
        if busy_lab(p, s, n):
            return redirect(lab_url(p, s, n), err=BUSY)
        st = load(p, s, n)
        if st.project is None:
            return redirect(lab_url(p, s, n), err="Проекта ещё нет: выполни лабу.")
        ctx.run(ctx.pipeline.key(st), ctx.pipeline.retry_project, st)
        return redirect(lab_url(p, s, n), msg="Собираю программу и делаю снимки заново")

    def project_dir(st) -> Path | None:
        d = Path(st.project.folder) if st.project and st.project.folder else None
        return d if d and d.is_dir() else None

    @app.post("/labs/{p}/{s}/{n}/project/open")
    def lab_project_open(p: str, s: str, n: str):
        d = project_dir(load(p, s, n, strict=False))
        if d is None:
            return redirect(lab_url(p, s, n), err="Папки проекта нет: нажми «Пересобрать».")
        _open_local(str(d))
        return redirect(lab_url(p, s, n))

    @app.get("/labs/{p}/{s}/{n}/project.zip")
    def lab_project_zip(p: str, s: str, n: str):
        import io
        import zipfile

        st = load(p, s, n, strict=False)
        d = project_dir(st)
        if d is None:
            return JSONResponse({"error": "нет проекта"}, status_code=404)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    z.write(f, f"{st.project.name}/{f.relative_to(d).as_posix()}")
        from urllib.parse import quote as _quote

        name = _quote(f"{st.project.name}.zip")
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})

    @app.get("/labs/{p}/{s}/{n}/page/{i}")
    def lab_page_png(p: str, s: str, n: str, i: int):
        st = load(p, s, n, strict=False)
        if not (1 <= i <= len(st.pages)) or not Path(st.pages[i - 1]).exists():
            return JSONResponse({"error": "нет страницы"}, status_code=404)
        return FileResponse(st.pages[i - 1], media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/labs/{p}/{s}/{n}/state")
    def lab_state(p: str, s: str, n: str):
        st = load(p, s, n, strict=False)
        key = ctx.pipeline.key(st)
        return {"status": st.status, "busy": key in ctx.running, "pages": len(st.pages)}

    @app.get("/labs/{p}/{s}/{n}/events")
    async def lab_events(request: Request, p: str, s: str, n: str, after: int = 0):
        key = f"{p}/{s}/{n}"

        async def gen():
            last = after
            for _ in range(800):  # ~40 минут по 3 с
                if await request.is_disconnected():
                    break
                # своё множество потоков: открытые страницы журнала не занимают общие потоки приложения
                evs = await asyncio.get_running_loop().run_in_executor(EVENT_WAITERS, ctx.bus.wait, key, last, 3.0)
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
        back = f"/semester?profile={_q(pid)}&subject={_q(sid)}"
        subj = get_subject(sid)
        profile = await asyncio.to_thread(ctx.store.get_profile, pid)
        if profile is None:
            return redirect(back, err="Профиль не найден")
        root = output_root(profile)
        name = f"{subj.short_name}_{(profile.full_name.split() or ['отчёт'])[0]}_семестр.docx"
        out = root / safe_name(subj.short_name) / safe_file_name(name)  # рядом со сданными лабами, путь виден
        try:
            path, rep = await asyncio.to_thread(ctx.pipeline.build_semester, pid, sid, numbers, out)
        except PipelineError as e:
            return redirect(back, err=str(e))
        except OSError as e:
            return redirect(back, err=os_error_text(e))
        notes = " ".join(getattr(rep, "warnings", None) or [])
        return redirect(back, msg=f"Отчёт за семестр сохранён: {path}" + (f" ({notes})" if notes else ""))

    # правила
    NOT_FOR_MEMBERS = "Правила для модели настраивает администратор. Свои пожелания пиши при создании лабы и в чате правок."

    @app.get("/rules", response_class=HTMLResponse)
    def rules_page(request: Request, file: str | None = None):
        if not can_tune_model():  # студенту правила не нужны, а их текст не должен утекать из программы
            return redirect("/", err=NOT_FOR_MEMBERS)
        files = ctx.rules().files()
        current = next((x for x in files if x.path == file), files[0] if files else None)
        return render(request, "rules.html", files=files, current=current, nav="rules")

    @app.post("/rules/save")
    def rules_save(path: str = Form(...), text: str = Form(...)):
        if not can_tune_model():
            return redirect("/", err=NOT_FOR_MEMBERS)
        try:
            ctx.rules().save_override(path, text)
        except KeyError:
            return redirect("/rules", err="Такого файла правил нет")
        ctx.reset()
        return redirect(f"/rules?file={_q(path)}", msg="Правило сохранено")

    @app.post("/rules/reset")
    def rules_reset(path: str = Form(...)):
        if not can_tune_model():
            return redirect("/", err=NOT_FOR_MEMBERS)
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
        admin = can_tune_model()
        return render(request, "settings.html", s=ctx.settings, presets=API_PRESETS, has_key=bool(get_api_key()),
                      show_mode=not _version.frozen() and admin, show_model=admin,
                      word=word_com.available(), soffice=libreoffice.find_soffice(ctx.settings.soffice_path),
                      nav="settings", my_balance=my_balance, balance_state=balance_state,
                      model_choices=MODEL_CHOICES)

    def can_tune_model() -> bool:
        """Режим, модель, адрес API и ключ: в облаке — только администратор (студент не уйдёт мимо подписки)."""
        if not ctx.cloud:
            return True
        try:
            m = ctx.store.member() if ctx.auth.session else None
        except CloudError:
            return False
        return bool(m and m.role == "admin" and m.approved)

    @app.post("/settings")
    async def settings_save(request: Request):
        if ctx.running:  # смена режима или модели посреди работы над лабой
            return redirect("/settings", err=BUSY_MESSAGE)
        f = await request.form()
        admin = can_tune_model()
        data = ctx.settings.model_dump()
        local_ok = admin and not _version.frozen()  # режим, адрес API и свой ключ — только вне установленной программы
        keys = ("soffice_path", "tesseract_path", "browser_channel") + (("model",) if admin else ()) + (
            ("mode", "base_url") if local_ok else ())
        for key in keys:
            if key in f:
                data[key] = (f.get(key) or "").strip() or None
        if _version.frozen():
            data["mode"] = "cloud"
        data["mode"] = data.get("mode") or "cloud"
        data["base_url"] = data.get("base_url") or Settings().base_url
        data["model"] = data.get("model") or Settings().model
        ctx.settings = Settings.model_validate(data)
        save_settings(ctx.settings)
        ctx.notice = ""
        key = (f.get("api_key") or "").strip() if local_ok else ""
        if key and not set_api_key(key):
            os.environ["FORLABS_API_KEY"] = key
        ctx.reset()
        return redirect("/settings", msg="Настройки сохранены")

    @app.post("/tour/done")
    def tour_done():
        if not ctx.settings.tour_done:
            ctx.settings = ctx.settings.model_copy(update={"tour_done": True})
            save_settings(ctx.settings)
        return Response(status_code=204)

    @app.post("/tour/restart")
    def tour_restart():
        ctx.settings = ctx.settings.model_copy(update={"tour_done": False})
        save_settings(ctx.settings)
        return redirect("/")

    @app.post("/settings/check")
    def settings_check():
        try:
            model = ctx.llm().check()
        except (LLMError, CloudError) as e:
            if "trial needs lab tag" in (getattr(e, "detail", "") or ""):  # пробный: модель отвечает только в лабе
                return redirect("/settings", msg="Связь с сервером есть. В пробном доступе модель отвечает только "
                                                 "внутри лабы, поэтому здесь её не проверить.")
            return redirect("/settings", err=getattr(e, "message_ru", str(e)))
        return redirect("/settings", msg=f"Связь есть, модель отвечает: {model}")

    register_admin(app, ctx, render, redirect)
    from forlabs.ui.support_routes import register_support

    register_support(app, ctx, render, redirect)

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
