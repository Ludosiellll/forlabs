"""UI Automation (System.Windows.Automation через pythonnet): заполнить поля, выбрать пункт, нажать кнопку,
прочитать текст окна. Работает с WinForms, WPF, Swing (через Java Access Bridge — не всегда), tkinter (частично).
Элемент ищется по AutomationId (у WinForms это имя элемента: inputA, btnCalc), иначе по видимому тексту."""

from __future__ import annotations

import threading
import time

_loaded = False


def load() -> None:
    global _loaded
    if _loaded:
        return
    import glob

    import clr  # pythonnet: в установленной программе есть (на нём работает окно pywebview)

    for asm in ("UIAutomationClient", "UIAutomationTypes"):
        try:
            clr.AddReference(f"{asm}, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35")
        except Exception:
            found = (glob.glob(rf"C:\Windows\Microsoft.NET\assembly\GAC_MSIL\{asm}\*\{asm}.dll")
                     + glob.glob(rf"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\WPF\{asm}.dll"))
            if not found:
                raise
            clr.AddReference(found[0])
    _loaded = True


class UiaError(Exception):
    pass


def _ns():
    load()
    import System.Windows.Automation as A  # noqa: N812

    return A


def root(hwnd: int):
    A = _ns()  # сначала загрузка сборок: без неё пространства имён System ещё нет
    from System import IntPtr

    return A.AutomationElement.FromHandle(IntPtr(hwnd))


def find(win, target: str, timeout: float = 5.0):
    """«id:inputA» — по AutomationId; «Вычислить» — по имени (видимому тексту), сначала точное совпадение."""
    A = _ns()
    end = time.monotonic() + timeout
    while True:
        if target.startswith("id:"):
            cond = A.PropertyCondition(A.AutomationElement.AutomationIdProperty, target[3:])
            el = win.FindFirst(A.TreeScope.Descendants, cond)
        else:
            el = win.FindFirst(A.TreeScope.Descendants,
                               A.PropertyCondition(A.AutomationElement.AutomationIdProperty, target))
            if el is None:
                el = win.FindFirst(A.TreeScope.Descendants, A.PropertyCondition(A.AutomationElement.NameProperty, target))
        if el is not None:
            return el
        if time.monotonic() > end:
            raise UiaError(f"в окне нет элемента «{target}»")
        time.sleep(0.2)


def _pattern(el, pattern):
    ok, p = el.TryGetCurrentPattern(pattern, None)
    return p if ok else None


def set_value(el, value: str) -> None:
    A = _ns()
    vp = _pattern(el, A.ValuePattern.Pattern)
    if vp is not None and not vp.Current.IsReadOnly:
        vp.SetValue(value)
        return
    if el.Current.ControlType == A.ControlType.ComboBox:
        select(el, value)
        return
    raise UiaError(f"в «{el.Current.Name or el.Current.AutomationId}» нельзя ввести текст")


def select(el, item: str) -> None:
    A = _ns()
    ec = _pattern(el, A.ExpandCollapsePattern.Pattern)
    if ec is not None:
        try:
            ec.Expand()
        except Exception:
            pass
        time.sleep(0.2)
    cond = A.PropertyCondition(A.AutomationElement.NameProperty, item)
    it = el.FindFirst(A.TreeScope.Descendants, cond)
    if it is None:
        raise UiaError(f"в списке нет пункта «{item}»")
    sp = _pattern(it, A.SelectionItemPattern.Pattern)
    if sp is None:
        raise UiaError(f"пункт «{item}» нельзя выбрать")
    sp.Select()
    if ec is not None:
        try:
            ec.Collapse()
        except Exception:
            pass


def click(el, timeout: float = 10.0) -> None:
    """Нажать кнопку/флажок/переключатель. Invoke может не вернуться, пока открыт MessageBox, — в потоке."""
    A = _ns()
    inv = _pattern(el, A.InvokePattern.Pattern)
    tog = _pattern(el, A.TogglePattern.Pattern)
    sel = _pattern(el, A.SelectionItemPattern.Pattern)
    if inv is not None:
        action = inv.Invoke
    elif tog is not None:
        action = tog.Toggle
    elif sel is not None:
        action = sel.Select
    else:
        raise UiaError(f"«{el.Current.Name or el.Current.AutomationId}» не нажимается")
    errors: list[Exception] = []

    def go():
        try:
            action()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(timeout if inv is None else 1.5)  # модальное окно держит Invoke — это нормально
    if errors:
        raise UiaError(f"нажатие не удалось: {errors[0]}")


def check(el, on: bool = True) -> None:
    A = _ns()
    tog = _pattern(el, A.TogglePattern.Pattern)
    if tog is None:
        raise UiaError("это не флажок")
    want = A.ToggleState.On if on else A.ToggleState.Off
    for _ in range(3):
        if tog.Current.ToggleState == want:
            return
        tog.Toggle()


def texts(win) -> list[str]:
    """Весь видимый текст окна: имена и значения элементов (для проверки ожидаемого результата)."""
    A = _ns()
    out: list[str] = []
    for el in win.FindAll(A.TreeScope.Subtree, A.Condition.TrueCondition):
        try:
            name = el.Current.Name
            if name:
                out.append(str(name))
            vp = _pattern(el, A.ValuePattern.Pattern)
            if vp is not None and vp.Current.Value:
                out.append(str(vp.Current.Value))
        except Exception:
            continue
    return out


def perform(hwnd: int, steps: list[dict], timeout: float = 5.0) -> None:
    """Шаги сценария: {"action": "set"|"select"|"click"|"check"|"uncheck"|"wait", "target": "...", "value": "..."}."""
    win = root(hwnd)
    for s in steps:
        action, target, value = s.get("action"), s.get("target") or "", s.get("value") or ""
        if action == "wait":
            time.sleep(min(float(value or 1), 10))
            continue
        el = find(win, target, timeout)
        if action == "set":
            set_value(el, value)
        elif action == "select":
            select(el, value)
        elif action == "click":
            click(el)
        elif action in ("check", "uncheck"):
            check(el, action == "check")
        else:
            raise UiaError(f"неизвестный шаг «{action}»")
        time.sleep(0.15)
