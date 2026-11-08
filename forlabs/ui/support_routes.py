"""Поддержка в приложении: страница студента и раздел администратора (переписки, подключение Telegram)."""

from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse

from forlabs.cloud.auth import CloudError
from forlabs.cloud.support import Support


def register_support(app: FastAPI, ctx, render, redirect) -> None:
    def support() -> Support | None:
        return Support(ctx.store) if ctx.cloud and ctx.auth.session else None

    def is_admin() -> bool:
        try:
            m = ctx.store.member()
        except CloudError:
            return False
        return bool(m and m.role == "admin" and m.approved)

    @app.get("/support")
    def support_page(request: Request):
        sup = support()
        if sup is None:
            return render(request, "support.html", nav="support", messages=[], cloud=False)
        try:
            messages = sup.messages()
            if any(m.from_admin and not m.read_at for m in messages):
                sup.mark_read()
        except CloudError as e:
            return render(request, "support.html", nav="support", messages=[], cloud=True, load_error=e.message_ru)
        return render(request, "support.html", nav="support", messages=messages, cloud=True, support_unread=0)

    @app.post("/support")
    def support_send(text: str = Form("")):
        sup = support()
        if sup is None:
            return redirect("/support")
        text = text.strip()
        if not text:
            return redirect("/support", err="Напиши, с чем нужна помощь")
        try:
            telegram = sup.send(text[:4000])
        except CloudError as e:
            return redirect("/support", err=e.message_ru)
        return redirect("/support", msg="Отправлено — ответ появится здесь" if telegram
                        else "Отправлено — администратор увидит сообщение в разделе «Админ»")

    @app.get("/admin/support")
    def admin_support(request: Request):
        sup = support()
        if sup is None or not is_admin():
            return RedirectResponse("/", status_code=303)
        threads = sup.threads()
        members = {m.user_id: m for m in ctx.store.list_members()}
        telegram, bot = sup.status()
        return render(request, "admin_support.html", nav="admin", threads=threads, members=members,
                      telegram=telegram, bot=bot, thread=None)

    @app.post("/admin/support/setup")
    def admin_support_setup(request: Request):
        sup = support()
        if sup is None or not is_admin():
            return RedirectResponse("/", status_code=303)
        threads = sup.threads()
        members = {m.user_id: m for m in ctx.store.list_members()}
        try:
            result, setup_error = sup.setup(), None
        except CloudError as e:
            result, setup_error = None, e.message_ru
        telegram, bot = sup.status()
        return render(request, "admin_support.html", nav="admin", threads=threads, members=members,
                      telegram=telegram, bot=bot, thread=None, setup=result, setup_error=setup_error)

    @app.get("/admin/support/{uid}")
    def admin_support_thread(request: Request, uid: str):
        sup = support()
        if sup is None or not is_admin():
            return RedirectResponse("/", status_code=303)
        messages = sup.messages(uid)
        if any(not m.from_admin and not m.read_at for m in messages):
            sup.admin_mark_read(uid)
        members = {m.user_id: m for m in ctx.store.list_members()}
        telegram, bot = sup.status()
        return render(request, "admin_support.html", nav="admin", threads=sup.threads(), members=members,
                      telegram=telegram, bot=bot, thread=uid, messages=messages)

    @app.post("/admin/support/{uid}")
    def admin_support_reply(uid: str, text: str = Form("")):
        sup = support()
        if sup is None or not is_admin():
            return RedirectResponse("/", status_code=303)
        if not text.strip():
            return redirect(f"/admin/support/{uid}", err="Пустой ответ")
        sup.reply(uid, text.strip()[:4000])
        return redirect(f"/admin/support/{uid}", msg="Ответ отправлен — студент увидит его в ForLabs")
