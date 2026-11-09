"""Когда снимок не вышел в песочнице: повторять ли запуск без неё и что сообщить этапу «Проект»."""

from pathlib import Path

from forlabs.runner import uia
from forlabs.runner.shots import RunTarget, ShotResult, WindowsShooter


def shooter(*answers):
    """Настоящая логика выбора песочницы, а запуск программы подменён ответами теста."""
    s = WindowsShooter.__new__(WindowsShooter)
    s.timeout, s.low, s.container, s._in_container, s._network = 5.0, True, True, False, False
    s.runs = []
    left = list(answers)

    def run(target, work, shot, out):
        s.runs.append("container" if s._in_container else "low")
        a = left.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    s._window = s._console = run
    s._stage = lambda target: Path("work")
    return s


TARGET = RunTarget(cmd=["app.exe"], cwd=Path("."))
SHOT = {"mode": "window", "steps": [], "expect": ""}


def test_missing_element_is_not_tried_again_without_sandbox():
    s = shooter(ShotResult(False, reason="в окне «Калькулятор» нет элемента «txtS»", kind="ui", ui="Окно …"))
    res = s._shoot(TARGET, SHOT, Path("out.png"))
    assert s.runs == ["container"]  # окно то же самое и без песочницы: второй запуск ничего не даст
    assert not res.ok and res.kind == "ui" and res.ui and res.sandbox == "container"


def test_wrong_output_in_sandbox_is_tried_without_it():
    s = shooter(ShotResult(False, reason="в окне нет ожидаемого «Итог»", kind="expect"), ShotResult(True))
    res = s._shoot(TARGET, SHOT, Path("out.png"))
    assert s.runs == ["container", "low"] and res.ok and res.sandbox == "low"


def test_launch_problem_in_sandbox_is_tried_without_it():
    s = shooter(ShotResult(False, reason="окно программы не появилось"), ShotResult(True))
    assert s._shoot(TARGET, SHOT, Path("out.png")).ok and s.runs == ["container", "low"]


def test_ui_error_without_sandbox_keeps_its_kind_for_the_fix():
    s = shooter(RuntimeError("песочница не создалась"), uia.UiaError("в списке «cmbOp» нет пункта «Сумма»"))
    res = s._shoot(TARGET, SHOT, Path("out.png"))
    assert not res.ok and res.kind == "ui" and "нет пункта «Сумма»" in res.reason
    assert "UiaError" not in res.reason  # студенту и модели — обычными словами


def test_outline_lines_read_like_a_form():
    rows = [("Edit", "txtFirst", "", "12", True, None), ("Edit", "txtResult", "", "", False, None),
            ("ComboBox", "cmbOperation", "", "", True, []), ("ComboBox", "cmbKind", "", "Круг", True, ["Круг", "Квадрат"]),
            ("Button", "btnCalculate", "Вычислить", "", True, None), ("Text", "lblFirst", "Первый операнд:", "", True, None),
            ("Button", "1234", "ОК", "", True, None)]
    text = uia.format_outline("Калькулятор", rows)
    assert text.splitlines()[0] == "Окно «Калькулятор»:"
    assert "[поле] txtFirst = «12»" in text
    assert "[поле] txtResult — пусто, только чтение" in text
    assert "[список] cmbOperation — пунктов нет" in text
    assert "[список] cmbKind = «Круг»; пункты: Круг, Квадрат" in text
    assert "[кнопка] btnCalculate «Вычислить»" in text
    assert "[надпись] lblFirst «Первый операнд:»" in text
    assert "[кнопка] «ОК»" in text and "1234" not in text  # номер вместо имени модели не нужен


def test_loose_match_finds_item_written_a_bit_differently():
    assert uia.loose_match(["Сложение", "Вычитание"], "сложение ") == "Сложение"
    assert uia.loose_match(["Сложение (+)", "Вычитание (-)"], "Сложение") == "Сложение (+)"
    assert uia.loose_match(["Сложение чисел", "Сложение дробей"], "Сложение") is None  # непонятно какой
    assert uia.loose_match([], "Сложение") is None
