"""Аудит-2, вёрстка в конвейере: без офиса, отчёт за семестр, сверка с каноном, снимки, папка сдачи."""

import io

import pytest
from PIL import Image
from test_run import ANS, FIXTURE, RAW, SEC, SEC2, png_bytes, setup

from forlabs.pipeline.run import PipelineError


def done(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    return pipe, st


# G2: нет ни Word, ни LibreOffice — лабу всё равно можно сдать
def test_render_without_office_can_be_submitted(tmp_home, tmp_path, monkeypatch):
    from forlabs.render import finalize as fin

    monkeypatch.setattr(fin.libreoffice, "find_soffice", lambda configured=None: None)
    pipe, st = done(tmp_home, tmp_path)
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    assert st.status == "rendered", st.error
    assert st.pdf_path is None and st.pages == [] and any("LibreOffice" in w for w in st.warnings)
    assert pipe.mark_final(st).status == "final"


# G3: недописанная лаба в отчёт за семестр не попадает молча
def test_semester_refuses_incomplete_lab(tmp_home, tmp_path):
    pipe, st = done(tmp_home, tmp_path)
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    st.report.questions = []
    pipe.store.save_lab(st)
    with pytest.raises(PipelineError, match="ЛР 7"):
        pipe.build_semester("ivanov", "psip", ["7"], tmp_path / "s.docx")


# G9: расхождения с каноном по тексту абзацев — предупреждение, не запрет сдачи
def test_canon_mismatch_does_not_block(tmp_home, tmp_path, monkeypatch):
    from forlabs.core.models import Finding
    from forlabs.pipeline import run

    monkeypatch.setattr(run, "compare", lambda docx: [Finding(stage="render", rule_id="render.canon_mismatch",
                                                             location="абзац 12", message="before_pt")])
    pipe, st = done(tmp_home, tmp_path)
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    assert all(f.severity == "warn" for f in st.findings if f.rule_id == "render.canon_mismatch")
    assert pipe.mark_final(st).status == "final"


# G12: очень высокий снимок — предупреждение
def test_tall_screenshot_warns(tmp_home, tmp_path):
    pipe, st = done(tmp_home, tmp_path)
    buf = io.BytesIO()
    Image.new("RGB", (500, 6000), (240, 240, 240)).save(buf, format="PNG")
    st = pipe.attach_screenshot(st, section=1, block=4, image=buf.getvalue())
    assert any("высокий" in e.text for e in pipe.bus.history(pipe.key(st)))


# G13: старый снимок WebP (до проверки при вставке) перекодируется при вёрстке, а не роняет её
def test_old_webp_screenshot_is_reencoded(tmp_home, tmp_path):
    pipe, st = done(tmp_home, tmp_path)
    rel = "assets/old.webp"
    f = pipe.lab_dir(st) / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 500), (200, 220, 240)).save(f, format="WEBP")
    st.report.sections[0].blocks[3].image_path = rel
    st.report.sections[0].blocks[3].slot = None
    st = pipe.render(st)
    assert st.status == "rendered", st.error
    assert st.report.sections[0].blocks[3].image_path.endswith(".png")


# H12: папка сдачи в кавычках («Копировать как путь») и относительная — работают
def test_output_root_quotes_and_relative(tmp_home, tmp_path):
    pipe, st = done(tmp_home, tmp_path)
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    prof = pipe.store.get_profile("ivanov")
    prof.output_root = f'"{tmp_path / "сдача2"}"'
    pipe.store.save_profile(prof)
    pipe.mark_final(st)
    assert list((tmp_path / "сдача2").rglob("*.docx"))


# C6: заменённый снимок, на который не ссылается ни одна версия, удаляется (место в облаке)
def test_replaced_screenshot_is_removed(tmp_home, tmp_path):
    pipe, st = done(tmp_home, tmp_path)
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    first = st.report.sections[0].blocks[3].image_path
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    assert not (pipe.lab_dir(st) / first).exists()
    assert (pipe.lab_dir(st) / st.report.sections[0].blocks[3].image_path).exists()


def test_screenshot_kept_when_a_version_uses_it(tmp_home, tmp_path):
    from forlabs.core.models import push_version

    pipe, st = done(tmp_home, tmp_path)
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    first = st.report.sections[0].blocks[3].image_path
    push_version(st, "правка")  # откат к этой версии вернёт и снимок
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    assert (pipe.lab_dir(st) / first).exists()
