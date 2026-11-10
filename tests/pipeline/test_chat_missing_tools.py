"""Программа не собрана или снимок не сделан, потому что на компьютере нет нужной программы. Чат не гадает и не
чинит код: говорит, чего нет, зачем оно и как поставить — кнопкой «Установить» или по официальной ссылке."""

from test_project_stage import PLAN, figures, make
from test_shot_repair import ScriptShooter

from forlabs.agent import apps
from forlabs.runner import languages, tools

WHY = {"kind": "answer", "reply": "На компьютере нет Python, поэтому программу нечем запустить."}
TRY_AND_FIX = {"kind": "answer", "reply": "Попробую ещё раз.", "reshoot": True, "project": "починить запуск"}


def lab_without_python(tmp_home, tmp_path, monkeypatch, then):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, *then], tools={}, monkeypatch=monkeypatch,
                          shooter=ScriptShooter(True, True))
    st = pipe.execute(st)
    assert st.project.missing == ["python"] and not figures(st)[0].image_path
    return pipe, fake, st


def said(call) -> str:
    return "\n".join(m["content"] for m in call)


def test_how_to_get_a_tool_forlabs_installs_itself():
    text = tools.how_to_get("dotnet")
    assert ".NET SDK" in text and "C#" in text  # что это и зачем
    assert "«Установить»" in text and "МБ" in text  # ForLabs поставит сам — где кнопка и сколько весит
    assert "https://dotnet.microsoft.com/" in text and "«Повторить»" in text  # или сам, и что нажать потом


def test_how_to_get_a_tool_only_the_student_can_install():
    text = tools.how_to_get("msvc")
    assert "Visual C++" in text and "https://visualstudio.microsoft.com/" in text
    assert "«Установить»" not in text and "сам" in text  # кнопки нет: только с сайта
    assert tools.how_to_get("нет такого") == ""


def test_how_to_get_a_program_for_screenshots():
    text = apps.how_to_get("visualstudio")
    assert "Visual Studio" in text and "https://" in text and "«Сделать снимки в программах»" in text
    assert "путь" in text  # программа стоит в необычном месте — можно указать, где она
    assert apps.how_to_get("нет такой") == ""


def test_chat_is_told_what_is_missing_and_how_to_get_it(tmp_home, tmp_path, monkeypatch):
    pipe, fake, st = lab_without_python(tmp_home, tmp_path, monkeypatch, [WHY])
    pipe.chat(st, "почему нет скриншота?")
    asked = said(fake.calls[-1])
    assert "Python 3" in asked and "python.org/downloads" in asked and "«Установить»" in asked
    system = fake.calls[-1][0]["content"]
    assert "нет нужной программы" in system.lower() and "не чини" in system.lower()


def test_try_again_without_the_tool_explains_and_does_not_touch_the_code(tmp_home, tmp_path, monkeypatch):
    pipe, fake, st = lab_without_python(tmp_home, tmp_path, monkeypatch, [TRY_AND_FIX])
    calls = len(fake.calls)
    st = pipe.chat(st, "попробуй ещё раз")
    assert len(fake.calls) == calls + 1  # только разбор сообщения: чинить код незачем
    reply = st.chat[-1].text
    assert "Python 3" in reply and "python.org/downloads" in reply and "«Установить»" in reply and "«Повторить»" in reply
    assert "Исправить не получилось" not in reply and "Программу исправил" not in reply


def test_after_the_tool_is_installed_try_again_takes_the_picture(tmp_home, tmp_path, monkeypatch):
    real = languages.detect
    pipe, fake, st = lab_without_python(tmp_home, tmp_path, monkeypatch, [TRY_AND_FIX])
    monkeypatch.setattr(languages, "detect", real)  # Python поставили
    st = pipe.chat(st, "поставил, попробуй ещё раз")
    assert st.project.built and figures(st)[0].image_path and "Снял сам" in st.chat[-1].text


def test_missing_program_for_a_screenshot_is_named_with_its_link(tmp_home, tmp_path, monkeypatch):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, WHY], shooter=ScriptShooter(True, True))
    st = pipe.execute(st)
    fig = figures(st)[1]
    fig.need_tool, fig.auto_note = "app:visualstudio", "Для снимка нужна программа «Visual Studio»."
    pipe._save(st)
    pipe.chat(st, "почему второй рисунок не снят?")
    asked = said(fake.calls[-1])
    assert "Visual Studio" in asked and "visualstudio.microsoft.com" in asked
