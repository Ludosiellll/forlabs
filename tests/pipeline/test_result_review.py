"""ForLabs смотрит, что программа на самом деле вывела, прежде чем вставить снимок в отчёт. Подпись на экране есть,
а вместо данных нули — такой снимок в отчёт не идёт: модель чинит программу и снимает заново; не вышло — рисунок
остаётся студенту с причиной."""

from pathlib import Path

from PIL import Image
from test_project_stage import GOOD, PLAN, figures, make

from forlabs.pipeline import project as proj
from forlabs.runner.shots import ShotResult

ZEROS = "Список отзывов:\n: 0\n: 0\nСредний рейтинг: 0,00"
RIGHT = "Список отзывов:\nНоутбук: 5\nМышь: 4\nСредний рейтинг: 4,50"
BAD = {"shots": [{"section": 1, "block": 3, "ok": False,
                  "problem": "названия товаров пустые и все рейтинги равны 0: данные после чтения потерялись"}]}
FINE = {"shots": [{"section": 1, "block": 3, "ok": True}]}
BETTER = GOOD.replace("Сумма:", "Сумма чисел:")


class Screens:
    """Снимок есть всегда, а на экране — то, что задано тестом."""

    def __init__(self, *screens):
        self.screens = list(screens)
        self.calls = []
        self.container = True

    def shoot(self, target, shot, out: Path) -> ShotResult:
        self.calls.append((target, shot))
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 200), (12, 12, 12)).save(out)
        return ShotResult(True, image=out, seen=self.screens.pop(0) if self.screens else RIGHT)


def said(call) -> str:
    return "\n".join(m["content"] for m in call)


def test_wrong_result_is_fixed_before_the_picture_goes_into_the_report(tmp_home, tmp_path):
    fix = {"files": [{"path": "main.py", "content": BETTER}]}
    shooter = Screens(ZEROS, RIGHT)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, BAD, fix, FINE], shooter=shooter)
    st = pipe.execute(st)
    assert figures(st)[0].image_path and len(shooter.calls) == 2  # снято заново, уже с исправленной программой
    assert st.project.shot_fixes == 1
    review, repair = said(fake.calls[5]), said(fake.calls[6])
    assert ": 0" in review and "Средний рейтинг: 0,00" in review  # модель видела настоящий экран
    assert "Результат работы программы" in review and "main.py" in review  # и какой это рисунок, и код
    assert "данные после чтения потерялись" in repair and ": 0" in repair  # чинит по найденной причине
    assert (tmp_path / "сдача" / "ПССИП" / "ЛР7" / "Проект Сумма" / "main.py").read_text(encoding="utf-8") == BETTER


def test_right_result_costs_one_look_and_changes_nothing(tmp_home, tmp_path):
    shooter = Screens(RIGHT)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, FINE], shooter=shooter)
    st = pipe.execute(st)
    assert figures(st)[0].image_path and len(shooter.calls) == 1 and st.project.shot_fixes == 0
    assert len(fake.calls) == 6  # план проекта и один взгляд на результат


def test_picture_with_a_wrong_result_never_stays_in_the_report(tmp_home, tmp_path):
    fix1 = {"files": [{"path": "main.py", "content": BETTER}]}
    fix2 = {"files": [{"path": "main.py", "content": BETTER + "\n"}]}
    shooter = Screens(ZEROS, ZEROS, ZEROS)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, BAD, fix1, BAD, fix2, BAD], shooter=shooter)
    st = pipe.execute(st)
    fig = figures(st)[0]
    assert not fig.image_path  # неверный снимок в отчёт не попал
    assert "неверный результат" in fig.auto_note and "рейтинги равны 0" in fig.auto_note  # и сказано почему
    assert st.status in ("rendered", "needs_manual")


def test_review_that_fails_does_not_break_the_lab(tmp_home, tmp_path):
    from forlabs.core.llm import LLMError

    shooter = Screens(RIGHT)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, LLMError("network", "нет связи")], shooter=shooter)
    st = pipe.execute(st)
    assert figures(st)[0].image_path and st.project.built  # проверить не вышло — снимок остаётся, как раньше


def test_nothing_to_look_at_means_no_review(tmp_home, tmp_path):
    shooter = Screens("")  # снимок без текста экрана (так бывает у окна без текста)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN], shooter=shooter)
    st = pipe.execute(st)
    assert figures(st)[0].image_path and len(fake.calls) == 5


def test_verdict_about_an_unknown_picture_is_ignored():
    out = proj.ReviewOut.model_validate({"shots": [{"section": 9, "block": 9, "ok": False, "problem": "нет"},
                                                    {"section": 1, "block": 3, "ok": "false", "problem": None}, "мусор"]})
    assert [(v.section, v.block, v.ok, v.problem) for v in out.shots] == [(9, 9, False, "нет"), (1, 3, False, "")]
