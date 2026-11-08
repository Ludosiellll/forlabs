"""Админ-панель: участники, подписки, ключи, расход, графики. Только облако и только администратор."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from forlabs.cloud.admin import MINSK, now_utc, parse_ts
from forlabs.cloud.auth import CloudError
from forlabs.core.store import safe_name
from forlabs.core.subjects import all_subjects
from forlabs.ui.charts import SERIES_COLORS, bars, fmt_tokens, stacked_bars

AVG_LAB_TOKENS = 30_000  # живые прогоны: 27–28 тыс. токенов на лабу
DAYS = 30
WEEKS = 8
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def labs_estimate(balance: int | None, avg: int = AVG_LAB_TOKENS) -> str:
    if balance is None:
        return ""
    n = int(balance // max(avg, 1))
    return f"≈ {fmt_tokens(n)} лаб" if n else "меньше чем на одну лабу"


def member_colors(members) -> dict[str, tuple[str, str]]:
    """Цвет закреплён за человеком, а не за местом в рейтинге: участники по порядку прихода,
    администраторы в конце. Больше пяти — первые четыре свои, остальные в «Остальные»."""
    ordered = [m for m in members if m.role != "admin"] + [m for m in members if m.role == "admin"]
    out: dict[str, tuple[str, str]] = {}
    fold = len(ordered) > len(SERIES_COLORS)
    for i, m in enumerate(ordered):
        if fold and i >= len(SERIES_COLORS) - 1:
            out[m.user_id] = ("Остальные", SERIES_COLORS[-1])
        else:
            out[m.user_id] = (m.name, SERIES_COLORS[i])
    return out


def last_days(n: int = DAYS) -> list[date]:
    today = datetime.now(MINSK).date()
    return [today - timedelta(days=n - 1 - i) for i in range(n)]


def daily_series(daily: list[tuple[str, str, int]], colors: dict[str, tuple[str, str]], days: list[date]):
    index = {d.isoformat(): i for i, d in enumerate(days)}
    series: dict[str, tuple[list[int], str]] = {}
    order: list[str] = []
    for day, uid, tokens in daily:
        if day not in index or uid not in colors:
            continue
        name, color = colors[uid]
        if name not in series:
            series[name] = ([0] * len(days), color)
            order.append(name)
        series[name][0][index[day]] += tokens
    by_color = {c: i for i, c in enumerate(SERIES_COLORS)}
    order.sort(key=lambda nm: by_color.get(series[nm][1], 99))
    return [(nm, series[nm][0], series[nm][1]) for nm in order]


def weekly_labs(labs, weeks: int = WEEKS) -> tuple[list[str], list[int]]:
    today = datetime.now(MINSK).date()
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
    counts = [0] * weeks
    for lab in labs:
        created = parse_ts(lab.created_at)
        if not created:
            continue
        k = (created.astimezone(MINSK).date() - start).days // 7
        if 0 <= k < weeks:
            counts[k] += 1
    labels = [(start + timedelta(weeks=i)).strftime("%d.%m") for i in range(weeks)]
    return labels, counts


def date_ru(value: str | None) -> str:
    dt = parse_ts(value)
    return dt.astimezone(MINSK).strftime("%d.%m.%Y") if dt else ""


def ago(value: str | None) -> str:
    dt = parse_ts(value)
    if not dt:
        return "—"
    s = (now_utc() - dt).total_seconds()
    if s < 3600:
        return "только что" if s < 120 else f"{int(s // 60)} мин назад"
    if s < 86400:
        return f"{int(s // 3600)} ч назад"
    if s < 2 * 86400:
        return "вчера"
    if s < 7 * 86400:
        return f"{int(s // 86400)} дн. назад"
    return date_ru(value)


def access_text(m) -> str:
    state = m.access_state
    if state == "pending":
        return "ждёт одобрения"
    if m.role == "admin":
        return "администратор"
    if state == "unlimited":
        return "без срока"
    if state == "expired":
        return f"закончилась {date_ru(m.access_until)}"
    left = m.days_left
    return f"до {date_ru(m.access_until)} · {left} дн."


def event_text(e) -> str:
    d = e.detail or {}
    if e.kind == "approve":
        return "Одобрен" if d.get("approved") else "Отключён"
    if e.kind == "access":
        until = d.get("until")
        if not until:
            return "Подписка без срока"
        dt = parse_ts(until)
        if dt and dt <= now_utc() + timedelta(minutes=1) and parse_ts(e.created_at) and \
                abs((dt - parse_ts(e.created_at)).total_seconds()) < 120:
            return "Подписка забрана"
        return f"Подписка до {date_ru(until)}"
    if e.kind == "note":
        return f"Заметка: {d.get('note') or '(пусто)'}"
    if e.kind == "name":
        return f"Имя: {d.get('name') or '(пусто)'}"
    if e.kind == "shared_key":
        return "Общий ключ разрешён" if d.get("allowed") else "Общий ключ запрещён"
    if e.kind == "key_set":
        return f"Ключ выдан (••••{d.get('last4', '')})"
    if e.kind == "key_clear":
        return "Ключ убран"
    return e.kind


def register_admin(app: FastAPI, ctx, render, redirect) -> None:
    def current_admin():
        if not ctx.cloud or not ctx.auth.session:
            return None
        try:
            m = ctx.store.member()
        except CloudError:
            return None
        return m if m and m.approved and m.role == "admin" else None

    def home():
        return RedirectResponse("/", status_code=303)

    def card_url(uid: str) -> str:
        return f"/admin/u/{uid}"

    @app.get("/users")
    def users_page():
        return RedirectResponse("/admin", status_code=303)

    @app.get("/admin")
    def admin_page(request: Request):
        me = current_admin()
        if me is None:
            return home()
        ov = ctx.store.admin_overview()
        days = last_days()
        colors = member_colors(ov.members)
        series = daily_series(ov.daily, colors, days)
        week_labels, week_counts = weekly_labs(ov.labs)
        since = now_utc() - timedelta(days=DAYS)
        labs_30 = [lab for lab in ov.labs if (parse_ts(lab.created_at) or since) > since]
        lab_counts: dict[str, list[int]] = {}
        for lab in ov.labs:
            c = lab_counts.setdefault(lab.owner, [0, 0])
            c[0] += 1
            c[1] += lab.status == "final"
        rows = []
        for m in ov.members:
            names = [p.full_name for p in ov.profiles.get(m.user_id, []) if p.full_name]
            rows.append({"m": m, "profiles": names, "key": ov.keys.get(m.user_id),
                         "usage": ov.usage_30d.get(m.user_id, 0), "labs": lab_counts.get(m.user_id, [0, 0]),
                         "color": colors.get(m.user_id, ("", ""))[1] if ov.usage_30d.get(m.user_id) else "",
                         "access": access_text(m), "is_me": m.user_id == me.user_id,
                         "seen": ago(m.last_seen_at)})
        active = sum(1 for m in ov.members if m.access_state in ("active", "expiring", "unlimited"))
        return render(request, "admin.html", nav="admin", me=me, rows=rows, active=active,
                      total_30=sum(ov.usage_30d.values()), labs_30=len(labs_30),
                      final_30=sum(1 for lab in labs_30 if lab.status == "final"),
                      usage_chart=stacked_bars([d.strftime("%d.%m") for d in days], series),
                      labs_chart=bars(week_labels, week_counts, fmt=lambda n: str(int(n)), width=440, height=220))

    @app.get("/admin/balances")
    def admin_balances():
        if current_admin() is None:
            return home()
        members = ctx.store.list_members()
        raw = ctx.store.balances([m.user_id for m in members], shared=True)
        out = {}
        for uid, (value, state) in raw.items():
            text = fmt_tokens(value) if state == "ok" else ("нет связи" if state == "offline" else
                                                            ("не задан" if uid == "shared" else "—"))
            out[uid] = {"tokens": value, "state": state, "text": text,
                        "labs": labs_estimate(value) if state == "ok" else ""}
        return JSONResponse(out)

    @app.get("/admin/u/{uid}")
    def admin_member_page(request: Request, uid: str):
        me = current_admin()
        if me is None:
            return home()
        card = ctx.store.admin_member(uid)
        if card is None:
            return redirect("/admin", err="Такого участника нет")
        m = card.member
        days = last_days()
        index = {d.isoformat(): i for i, d in enumerate(days)}
        values = [0] * len(days)
        for day, tokens in card.daily:
            if day in index:
                values[index[day]] += tokens
        ov_members = ctx.store.list_members()
        colors = member_colors(ov_members)
        color = colors.get(uid, (m.name, SERIES_COLORS[0]))[1]
        lab_tokens = [t for t, _ in card.usage_by_lab.values() if t > 0]
        avg = int(sum(lab_tokens) / len(lab_tokens)) if lab_tokens else AVG_LAB_TOKENS
        subjects = all_subjects()
        labs = []
        for lab in sorted(card.labs, key=lambda x: x.created_at or "", reverse=True):
            tokens, calls = card.usage_by_lab.get(lab.key, (0, 0))
            subj = subjects.get(lab.subject_id)
            labs.append({"lab": lab, "subject": subj.short_name if subj else lab.subject_id, "tokens": tokens,
                         "calls": calls, "created": date_ru(lab.created_at)})
        lab_keys = {lab.key for lab in card.labs}
        unmatched = sum(t for k, (t, _) in card.usage_by_lab.items() if k not in lab_keys)
        return render(request, "admin_member.html", nav="admin", me=me, card=card, m=m, is_me=uid == me.user_id,
                      unmatched=unmatched,
                      access=access_text(m), seen=ago(m.last_seen_at), joined=date_ru(m.created_at),
                      chart=bars([d.strftime("%d.%m") for d in days], values, color=color, name=m.name),
                      labs=labs, avg=avg, events=[(date_ru(e.created_at), event_text(e)) for e in card.events],
                      key_set=date_ru(card.key.set_at) if card.key else "", subjects=subjects,
                      until_value=(parse_ts(m.access_until).astimezone(MINSK).date().isoformat()
                                   if parse_ts(m.access_until) else ""))

    @app.post("/admin/u/{uid}/access")
    def admin_access(uid: str, action: str = Form(...), until: str = Form("")):
        me = current_admin()
        if me is None:
            return home()
        if uid == me.user_id:
            return redirect(card_url(uid), err="Свой доступ администратор не меняет")
        messages = {"extend7": "Подписка продлена на 7 дней", "extend30": "Подписка продлена на 30 дней",
                    "until": "Срок подписки изменён", "unlimited": "Подписка без срока",
                    "revoke": "Подписка забрана", "approve": "Участник одобрен", "disable": "Участник отключён"}
        if action not in messages:
            return redirect(card_url(uid), err="Непонятное действие")
        if action == "extend7":
            ctx.store.admin_extend(uid, 7)
        elif action == "extend30":
            ctx.store.admin_extend(uid, 30)
        elif action == "until":
            if not _DAY.match(until.strip()):
                return redirect(card_url(uid), err="Выбери дату окончания")
            ctx.store.admin_set_until(uid, until.strip())
        elif action == "unlimited":
            ctx.store.admin_set_until(uid, None)
        elif action == "revoke":
            ctx.store.admin_revoke(uid)
        else:
            ctx.store.admin_update_member(uid, approved=action == "approve")
        return redirect(card_url(uid), msg=messages[action])

    @app.post("/admin/u/{uid}/key")
    def admin_key(uid: str, action: str = Form(...), api_key: str = Form("")):
        if current_admin() is None:
            return home()
        if action == "set":
            try:
                last4 = ctx.store.admin_set_key(uid, api_key)
            except ValueError as e:
                return redirect(card_url(uid), err=str(e))
            return redirect(card_url(uid), msg=f"Ключ сохранён (••••{last4})")
        if action == "clear":
            ctx.store.admin_clear_key(uid)
            return redirect(card_url(uid), msg="Ключ убран")
        if action in ("shared_on", "shared_off"):
            ctx.store.admin_update_member(uid, allow_shared_key=action == "shared_on")
            return redirect(card_url(uid), msg="Сохранено")
        return redirect(card_url(uid), err="Непонятное действие")

    @app.post("/admin/u/{uid}/note")
    def admin_note(uid: str, display_name: str = Form(""), note: str = Form("")):
        if current_admin() is None:
            return home()
        ctx.store.admin_update_member(uid, display_name=display_name.strip()[:120], note=note.strip()[:2000])
        return redirect(card_url(uid), msg="Сохранено")

    @app.get("/admin/u/{uid}/lab/{p}/{s}/{n}.docx")
    def admin_lab_docx(uid: str, p: str, s: str, n: str):
        if current_admin() is None:
            return home()
        local = ctx.store.local.home / "admin_downloads" / safe_name(uid) / safe_name(p) / safe_name(s) / \
            f"ЛР{safe_name(n)}.docx"
        if not ctx.store.download_member_report(uid, p, s, n, local):
            return redirect(card_url(uid), err=f"Отчёта ЛР {n} в облаке нет — участник его ещё не свёрстал")
        subj = all_subjects().get(s)
        return FileResponse(local, filename=f"ЛР{n}_{subj.short_name if subj else s}_{p}.docx",
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
