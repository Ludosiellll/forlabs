"""Шаги сценария на странице сайта через Playwright: поля, кнопки, наведение курсора, прокрутка, переход на
другую страницу сайта. Работает со страницей уже открытого браузера (Edge с портом отладки): окно снимается как
обычно, вместе с адресной строкой, а наведение — без настоящей мыши студента.

Что видно на странице, считается по экрану, а не по всему документу: текст ниже первого экрана на снимок не
попадает и «увиденным» не считается."""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from urllib.parse import urljoin, urlsplit

KEYS = {"enter": "Enter", "tab": "Tab", "esc": "Escape", "escape": "Escape", "space": "Space", "backspace": "Backspace",
        "delete": "Delete", "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
        "home": "Home", "end": "End", "pageup": "PageUp", "pagedown": "PageDown"}
SETTLE = 0.6  # после шага: переходы CSS и обработчики успевают отработать


class StepError(Exception):
    """Шаг на странице не выполнился: причина — по-русски, для студента и для модели, которая чинит сценарий."""


_CSS_LIKE = re.compile(r"^[.#\[]|[>~+]| \w*[.#\[:]|^\w+[.#\[:]\S|:nth|::")
_STATE_JS = """() => {
  const vh = innerHeight, vw = innerWidth;
  const inView = (b) => b.width > 0 && b.height > 0 && b.bottom > 0 && b.top < vh && b.right > 0 && b.left < vw;
  const shown = (el) => {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const st = getComputedStyle(e);
      if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) return false;
    }
    return true;
  };
  const text = [];
  const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode, t = node.textContent.replace(/\\s+/g, ' ').trim();
    const el = node.parentElement;
    if (!t || !el || ['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(el.tagName) || !shown(el)) continue;
    const r = document.createRange();
    r.selectNodeContents(node);
    if ([...r.getClientRects()].some(inView)) text.push(t);
  }
  const fields = [];
  for (const e of document.querySelectorAll('input, textarea, select')) {
    if (e.type === 'hidden' || e.type === 'password' || !shown(e) || !inView(e.getBoundingClientRect())) continue;
    const name = e.id || e.name || e.type;
    const value = e.type === 'checkbox' || e.type === 'radio' ? (e.checked ? 'отмечен' : 'не отмечен')
      : e.tagName === 'SELECT' ? (e.selectedOptions[0] ? e.selectedOptions[0].textContent.trim() : '') : e.value;
    fields.push(name + ' = ' + value);
  }
  const broken = [...document.images].filter((i) => i.getAttribute('src') && i.complete && i.naturalWidth === 0)
    .map((i) => i.getAttribute('src'));
  const hovered = [...document.querySelectorAll(':hover')].pop();
  const under = hovered && hovered !== document.body && hovered !== document.documentElement
    ? (hovered.innerText || hovered.getAttribute('alt') || hovered.id || hovered.tagName).replace(/\\s+/g, ' ').trim()
    : '';
  return {text, fields, broken: [...new Set(broken)], under: under.slice(0, 80), title: document.title,
          y: Math.round(scrollY), height: document.documentElement.scrollHeight, vh};
}"""


def locate(page, target: str, timeout: float = 8.0):
    """Элемент страницы так, как его назвала модель: «id:имя», имя id или name, CSS-селектор («.btn-fill»,
    «nav li:nth-child(2)»), текст кнопки, ссылки или надписи. Нет такого — StepError."""
    t = (target or "").strip()
    if not t:
        raise StepError("в шаге не назван элемент страницы")
    tries = []
    low = t.lower()
    if low.startswith("id:"):
        tries.append(lambda: page.locator(f'[id="{_q(t[3:].strip())}"]'))
    elif low.startswith(("css:", "selector:")):
        tries.append(lambda: page.locator(t.split(":", 1)[1].strip()))
    else:
        if re.fullmatch(r"[\w-]+", t):
            tries.append(lambda: page.locator(f'[id="{_q(t)}"], [name="{_q(t)}"]'))
        if _CSS_LIKE.search(t):
            tries.append(lambda: page.locator(t))
        tries += [lambda: page.get_by_role("button", name=t, exact=True), lambda: page.get_by_role("link", name=t, exact=True),
                  lambda: page.get_by_text(t, exact=True), lambda: page.get_by_label(t, exact=True),
                  lambda: page.get_by_placeholder(t), lambda: page.get_by_text(t), lambda: page.get_by_alt_text(t)]
    deadline = time.time() + timeout
    while True:
        for make in tries:
            try:
                loc = make()
                if loc.count():
                    return loc.first
            except Exception:  # noqa: BLE001 — строка не годится как селектор: пробуем следующий способ
                continue
        if time.time() >= deadline:
            raise StepError(f"элемента «{t}» на странице нет")
        time.sleep(0.25)


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _inside_site(page, value: str) -> str:
    """Адрес страницы того же сайта для шага open; всё вне папки (или сервера) сайта — StepError."""
    v = (value or "").strip()
    here = urlsplit(page.url)
    if not v or re.match(r"^[a-z][a-z0-9+.-]*:", v, re.I) or v.startswith("//") or ".." in v.replace("\\", "/").split("/"):
        raise StepError(f"шаг open открывает только страницы этого сайта, а не «{v}»")
    if here.scheme == "file":
        return urljoin(page.url, v.lstrip("/"))
    return urljoin(f"{here.scheme}://{here.netloc}/", v.lstrip("/"))


def perform(page, steps, timeout: float = 8.0) -> None:
    """Шаги сценария по порядку. set, select, click, check, uncheck, hover, scroll, type, key, wait, open."""
    for s in steps:
        get = s.get if isinstance(s, dict) else lambda k, d="", s=s: getattr(s, k, d)
        action, target, value = (get("action") or "").strip().lower(), get("target") or "", get("value") or ""
        try:
            if action == "wait":
                time.sleep(min(float(value or 1), 10))
                continue
            if action == "type":
                page.keyboard.type(value)
            elif action == "key":
                page.keyboard.press(KEYS.get(value.strip().lower(), value.strip()))
            elif action == "open":
                page.goto(_inside_site(page, value), wait_until="load", timeout=timeout * 1000)
            elif action == "scroll" and not target.strip():
                v = value.strip().lower()
                where = {"bottom": "document.documentElement.scrollHeight", "вниз": "document.documentElement.scrollHeight",
                         "top": "0", "вверх": "0"}.get(v)
                if where is not None:
                    page.evaluate(f"window.scrollTo(0, {where})")
                else:
                    page.evaluate("(dy) => window.scrollBy(0, dy)", int(float(v or 600)))
            else:
                el = locate(page, target, timeout)
                if action == "scroll":
                    el.evaluate("(e) => e.scrollIntoView({block: 'center', inline: 'nearest'})")
                elif action == "hover":
                    el.evaluate("(e) => e.scrollIntoView({block: 'center', inline: 'nearest'})")
                    el.hover(timeout=timeout * 1000, force=True)
                elif action == "set":
                    el.fill(value, timeout=timeout * 1000)
                elif action == "select":
                    try:
                        el.select_option(label=value, timeout=timeout * 1000)
                    except Exception:  # noqa: BLE001 — пункт назван значением, а не подписью
                        el.select_option(value=value, timeout=timeout * 1000)
                elif action == "click":
                    el.click(timeout=timeout * 1000)
                elif action in ("check", "uncheck"):
                    el.set_checked(action == "check", timeout=timeout * 1000)
                else:
                    raise StepError(f"неизвестный шаг «{action}»")
        except StepError:
            raise
        except Exception as e:  # noqa: BLE001 — Playwright: элемент закрыт другим, не поле, время вышло
            what = f"«{target}»" if target else f"«{value}»"
            raise StepError(f"шаг {action} {what} не выполнился: {str(e).splitlines()[0][:160]}") from e
        time.sleep(0.15)
    if steps:
        time.sleep(SETTLE)
        try:  # шаг мог отправить форму или перейти по ссылке: ждём новую страницу
            page.wait_for_load_state("load", timeout=5000)
        except Exception:  # noqa: BLE001
            pass


def reveal(page, expect: str) -> bool:
    """Ожидаемый текст есть на странице, но ниже экрана — прокрутить к нему (иначе на снимке его не будет).
    → виден ли он теперь."""
    want = " ".join((expect or "").split())
    if not want:
        return True
    if _seen(want, page_state(page)):
        return True
    try:
        loc = page.get_by_text(want)
        if not loc.count():
            return False
        loc.first.evaluate("(e) => e.scrollIntoView({block: 'center', inline: 'nearest'})")
        time.sleep(0.3)
    except Exception:  # noqa: BLE001
        return False
    return _seen(want, page_state(page))


POSITIONING = ("scroll", "hover")  # шаги, которыми сценарий сам выбрал, что будет в кадре


def check_expect(page, expect: str, steps, typed: str = "") -> tuple[bool, str]:
    """Есть ли ожидаемое и что сейчас видно. Сценарий сам навёл или прокрутил — страница остаётся как есть, а
    ожидаемому достаточно быть на странице; иначе текст ниже экрана прокручивается в кадр и должен быть виден.
    typed — что сценарий набрал сам: значение в поле ещё не результат."""
    from forlabs.runner.shots import shown

    def action(s) -> str:
        return ((s.get("action") if isinstance(s, dict) else getattr(s, "action", "")) or "").lower()

    want = " ".join((expect or "").split())
    if not want:
        return True, page_state(page)
    if any(action(s) in POSITIONING for s in steps or []):
        state = page_state(page)
        if shown(want, state, typed):
            return True, state
        try:
            whole = page.evaluate("() => (document.body ? document.body.innerText : '') + ' ' + document.title")
        except Exception:  # noqa: BLE001
            whole = ""
        return shown(want, whole, typed), state
    reveal(page, want)
    state = page_state(page)
    return shown(want, state, typed), state


def _seen(want: str, state: str) -> bool:
    squash = lambda x: re.sub(r"\s+", "", x).lower()  # noqa: E731
    return squash(want) in squash(state)


def page_state(page) -> str:
    """Что сейчас видно на экране: текст в кадре, значения полей, что под курсором, какие картинки не загрузились.
    Это и проверка expect, и то, что проверяющая модель знает о снимке."""
    try:
        s = page.evaluate(_STATE_JS)
    except Exception as e:  # noqa: BLE001 — страница закрылась или ушла на адрес без документа
        return f"[страницу прочитать не удалось: {str(e).splitlines()[0][:120]}]"
    lines = [f"Страница: {s['title']}"] if s.get("title") else []
    lines += s["text"]
    lines += [f"Поле {f}" for f in s["fields"]]
    if s["under"]:
        lines.append(f"Под курсором: {s['under']}")
    if s["y"]:
        lines.append(f"[страница прокручена на {s['y']} из {max(s['height'] - s['vh'], 0)} точек]")
    if s["broken"]:
        lines.append("[картинки не загрузились: " + ", ".join(s["broken"][:8]) + "]")
    return "\n".join(lines)


@contextmanager
def attach(port: int, url: str = "", timeout: float = 8.0):
    """Страница браузера, запущенного с портом отладки (--remote-debugging-port). Даёт None, если подключиться не
    вышло: вызывающий снимает как раньше, без наведения и прокрутки."""
    pw = browser = None
    try:
        try:
            from playwright.sync_api import sync_playwright

            pw = sync_playwright().start()
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=timeout * 1000)
            pages = [pg for ctx in browser.contexts for pg in ctx.pages]
            mine = [pg for pg in pages if url and pg.url.split("#")[0] == url.split("#")[0]]
            real = [pg for pg in pages if not pg.url.startswith(("edge:", "chrome:", "devtools:", "about:"))]
            page = (mine or real or pages or [None])[0]
        except Exception:  # noqa: BLE001 — нет Playwright, порт занят, браузер не ответил
            page = None
        yield page
    finally:
        for close in (getattr(browser, "close", None), getattr(pw, "stop", None)):
            try:
                if close:
                    close()
            except Exception:  # noqa: BLE001
                pass
