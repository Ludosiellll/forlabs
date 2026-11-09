"""Поддержка в приложении: страница студента и раздел администратора (переписки, подключение Telegram)."""

from __future__ import annotations

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from forlabs.cloud.auth import CloudError
from forlabs.cloud.support import MAX_IMAGES, Support, valid_path


def register_support(app: FastAPI, ctx, render, redirect) -> None:
    def support() -> Support | None:
        return Support(ctx.store) if ctx.cloud and ctx.auth.session else None

    def is_admin() -> bool:
        try:
            m = ctx.store.member()
        except CloudError:
            return False
        return bool(m and m.role == "admin" and m.approved)

    async def read_images(images: list[UploadFile] | None) -> list[bytes]:
        files = [f for f in (images or []) if f is not None and (f.filename or "")]
        if len(files) > MAX_IMAGES:
            raise ValueError(f"Можно прикрепить не больше {MAX_IMAGES} фото")
        return [await f.read() for f in files]

    def my_messages(sup: Support):
        messages = sup.messages()
        ctx.store._support_unread = None
        if any(m.from_admin and not m.read_at for m in messages):
            sup.mark_read()
        return messages

    @app.get("/support/log", response_class=HTMLResponse)
    def support_log(request: Request):
        sup = support()
        if sup is None:
            return HTMLResponse("", status_code=404)
        try:
            messages = my_messages(sup)
        except CloudError:
            return HTMLResponse("", status_code=503)
        return render(request, "_support_log.html", messages=messages, admin_view=False)

    @app.get("/support/img")
    def support_img(path: str = ""):
        sup = support()
        if sup is None or not valid_path(path):
            return Response(status_code=404)
        if not path.startswith(f"{ctx.auth.user_id}/") and not is_admin():
            return Response(status_code=404)
        try:
            data = sup.image(path)
        except CloudError:
            return Response(status_code=503)
        if data is None:
            return Response(status_code=404)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/support")
    def support_page(request: Request):
        sup = support()
        if sup is None:
            return render(request, "support.html", nav="support", messages=[], cloud=False)
        try:
            messages = my_messages(sup)
        except CloudError as e:
            return render(request, "support.html", nav="support", messages=[], cloud=True, load_error=e.message_ru)
        return render(request, "support.html", nav="support", messages=messages, cloud=True, support_unread=0)

    @app.post("/support")
    async def support_send(text: str = Form(""), images: list[UploadFile] | None = File(None)):
        sup = support()
        if sup is None:
            return redirect("/support")
        text = text.strip()
        try:
            data = await read_images(images)
            if not text and not data:
                return redirect("/support", err="Напиши, с чем нужна помощь, или приложи снимок экрана")
            telegram = sup.send(text[:4000], data)
        except ValueError as e:
            return redirect("/support", err=str(e))
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

    @app.get("/admin/support/{uid}/log", response_class=HTMLResponse)
    def admin_support_log(request: Request, uid: str):
        sup = support()
        if sup is None or not is_admin():
            return HTMLResponse("", status_code=404)
        messages = sup.messages(uid)
        if any(not m.from_admin and not m.read_at for m in messages):
            sup.admin_mark_read(uid)
        members = {m.user_id: m for m in ctx.store.list_members()}
        return render(request, "_support_log.html", messages=messages, admin_view=True, who=members.get(uid))

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
    async def admin_support_reply(uid: str, text: str = Form(""), images: list[UploadFile] | None = File(None)):
        sup = support()
        if sup is None or not is_admin():
            return RedirectResponse("/", status_code=303)
        try:
            data = await read_images(images)
            if not text.strip() and not data:
                return redirect(f"/admin/support/{uid}", err="Пустой ответ")
            sup.reply(uid, text.strip()[:4000], data)
        except ValueError as e:
            return redirect(f"/admin/support/{uid}", err=str(e))
        return redirect(f"/admin/support/{uid}", msg="Ответ отправлен — студент увидит его в ForLabs")
