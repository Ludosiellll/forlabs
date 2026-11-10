"""Страница лабы: когда программа не собралась, студент видит сами ошибки компилятора, а не только «ошибки
остались»; у лабы из нескольких программ названы программы и сказано, что открывать."""

from test_appshots_ui import lab

LOG = "CalculatorApp/Program.cs(8,21): error CS0017: Program has more than one entry point defined. <Main>"


def state(ctx, url):
    profile, subject, number = url.rstrip("/").split("/")[-3:]
    return ctx.store.load_lab(profile, subject, number)


def test_build_errors_are_shown_under_the_program(tmp_home, tmp_path, monkeypatch):
    c, ctx, url = lab(tmp_home, tmp_path, monkeypatch)
    st = state(ctx, url)
    st.project.built, st.project.build_log = False, LOG
    st.project.note = "Программу не собрал: ошибки остались. Проект в папке сдачи, снимки сделай сам."
    ctx.store.save_lab(st)
    page = c.get(url).text
    left = page[page.index('class="lab-left"'):page.index('class="lab-center"')]
    assert "Ошибки сборки" in left and "error CS0017" in left
    assert "&lt;Main&gt;" in left and "<Main>" not in left  # текст компилятора — как текст, не как разметка


def test_no_error_block_when_the_program_is_built(tmp_home, tmp_path, monkeypatch):
    c, ctx, url = lab(tmp_home, tmp_path, monkeypatch)
    assert state(ctx, url).project.built
    assert "Ошибки сборки" not in c.get(url).text


def test_several_programs_are_named_with_what_to_open(tmp_home, tmp_path, monkeypatch):
    c, ctx, url = lab(tmp_home, tmp_path, monkeypatch)
    st = state(ctx, url)
    st.project.language, st.project.programs = "csharp", ["CalculatorApp", "ClassLibrary1", "LibraryTestApp"]
    ctx.store.save_lab(st)
    page = c.get(url).text
    left = page[page.index('class="lab-left"'):page.index('class="lab-center"')]
    assert "CalculatorApp, ClassLibrary1, LibraryTestApp" in left and ".sln" in left
    st.project.language = "python"
    ctx.store.save_lab(st)
    assert ".sln" not in c.get(url).text  # файл решения — только у C#


def test_parse_finding_that_blocks_hand_in_can_be_seen_and_accepted(tmp_home, tmp_path, monkeypatch):
    from forlabs.core.models import Finding

    c, ctx, url = lab(tmp_home, tmp_path, monkeypatch)
    st = state(ctx, url)
    st.status = "rendered"
    for sec in st.report.sections:  # все рисунки на месте: мешать сдаче может только замечание
        for b in sec.blocks:
            if b.type == "figure" and not b.image_path:
                b.image_path = "assets/x.png"
    st.findings = [Finding(stage="spec", rule_id="spec.not_verbatim", severity="error", location="Задание 1",
                           text="Создать базу данных.", message="Формулировки нет в методичке дословно — проверь и поправь")]
    ctx.store.save_lab(st)
    page = c.get(url).text
    assert 'href="#notes"' in page and 'id="notes"' in page  # ссылка «с замечаниями» ведёт туда, где они есть
    notes = page[page.index('id="notes"'):]
    assert "Формулировки нет в методичке дословно" in notes and "Оставить как есть" in notes
    r = c.post(url + "/accept", data={"rule": "spec.not_verbatim|Задание 1"}, follow_redirects=False)
    assert r.status_code == 303
