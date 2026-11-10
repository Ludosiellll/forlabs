"""Программы для лаб: состояние, установка, загрузка по ссылке. Доступно и студентам — это их компьютер."""

from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from forlabs.runner.tools import ToolError


def register_tools(app: FastAPI, ctx, render, redirect) -> None:
    def unknown(key: str) -> JSONResponse | None:
        if key not in ctx.tools.tools:
            return JSONResponse({"ok": False, "error": "Такой программы нет."}, status_code=404)
        return None

    @app.get("/tools/status")
    def tools_status(refresh: int = 0):
        """Для опроса страницы: состояние каждой программы и загрузки по ссылке. refresh=1 — поискать заново."""
        return ctx.tools.status(refresh=bool(refresh))

    @app.post("/tools/{key}/install")
    def tools_install(key: str):
        bad = unknown(key)
        if bad:
            return bad
        try:
            if ctx.synchronous:
                ctx.tools.install(key)
            else:
                ctx.tools.start_install(key)
        except ToolError as e:  # MSVC, Edge — только с сайта
            return JSONResponse({"ok": False, "error": str(e), "page": ctx.tools.tools[key].page}, status_code=409)
        return {"ok": True, **ctx.tools.tool_status(key)}

    @app.get("/tools/{key}/missing", response_class=HTMLResponse)
    def tools_missing(request: Request, key: str):
        """Блок «нет программы» для страницы лабы (тот же, что включается в шаблон через _tool_missing.html)."""
        if key not in ctx.tools.tools:
            return HTMLResponse("", status_code=404)
        return render(request, "_tool_missing.html", tool=ctx.tools.tool_status(key))

    @app.post("/tools/link")
    def tools_link(url: str = Form(""), key: str = Form(""), go: str = Form("")):
        """Своя ссылка: без go — узнать сайт, имя и размер; go=1 — скачать в «Загрузки» и открыть."""
        try:
            if go == "1":
                link = ctx.tools.start_link(url, key or None, wait=ctx.synchronous)
            else:
                link = ctx.tools.check_link(url, key or None)
        except ToolError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        return {"ok": True, "link": link}

    @app.post("/tools/link/open")
    def tools_link_open():
        return {"ok": ctx.tools.open_link(), "link": ctx.tools.link_status()}

    @app.post("/tools/link/unpack")
    def tools_link_unpack():
        """Скачанный по ссылке архив известной программы — распаковать в папку ForLabs вместо открытия."""
        try:
            ok = ctx.tools.install_from_link(wait=ctx.synchronous)
        except ToolError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=409)
        if not ok:
            return JSONResponse({"ok": False, "error": "Это не архив программы для лаб."}, status_code=409)
        key = ctx.tools.link.unpack
        return {"ok": True, **ctx.tools.tool_status(key)}
