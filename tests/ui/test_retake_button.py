"""Страница лабы: рисунок, который ForLabs не снял сам, можно попросить снять ещё раз — кнопка в «Нужно от тебя»."""

from test_appshots_ui import lab

from forlabs.runner.shots import FakeShooter, ShotResult


def test_retake_button_appears_for_pictures_forlabs_failed_to_take(tmp_home, tmp_path, monkeypatch):
    monkeypatch.setattr(FakeShooter, "shoot", lambda self, target, shot, out: ShotResult(
        False, reason="в списке «cmbOperation» нет пункта «Сложение» (список пуст)"))
    c, ctx, url = lab(tmp_home, tmp_path, monkeypatch)
    page = c.get(url).text
    assert "Сам не снял: в списке «cmbOperation» нет пункта «Сложение» (список пуст)." in page
    assert "Снять ещё раз" in page and page.count(url + "/project/retry") >= 2  # и в «Нужно от тебя», и у программы


def test_no_retake_button_when_everything_is_taken(tmp_home, tmp_path, monkeypatch):
    c, ctx, url = lab(tmp_home, tmp_path, monkeypatch)
    assert "Снять ещё раз" not in c.get(url).text
