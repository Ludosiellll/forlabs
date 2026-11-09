"""Свой предмет прямо в профиле студента: если нужного нет в списке — добавить, он сразу отмечен у студента."""

import pytest
from fastapi.testclient import TestClient
from test_routes import make_profile

from forlabs.core.settings import Settings
from forlabs.core.subjects import all_subjects
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

PID = "ivanov_ivan_petrovich"


@pytest.fixture
def client(tmp_home, tmp_path):
    ctx = AppContext(settings=Settings(mode="local", tour_done=True), prefer_word=False, synchronous=True)
    c = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(c, tmp_path)
    return c, ctx


def _form(**extra):
    return {"id": PID, "full_name": "Иванов Иван Петрович", "title_name": "И.П. Иванов", "gender": "m",
            "group": "РС02-23", "enr_psip_on": "on", "enr_psip_variant": "14", **extra}


def test_profile_form_offers_own_subject(client):
    c, _ = client
    for url in ("/profiles/new", f"/profiles/{PID}"):
        page = c.get(url).text
        assert "Добавить свой предмет" in page and 'name="new_subject_name"' in page


def test_own_subject_created_and_enrolled_on_save(client):
    c, ctx = client
    r = c.post("/profiles/save", data=_form(new_subject_name="Технология разработки программного обеспечения",
                                            new_subject_short="", new_subject_kind="code",
                                            new_subject_teacher="С.С. Сидоров", new_subject_variant="5",
                                            new_subject_style="short"), follow_redirects=False)
    assert r.status_code == 303
    subj = next(s for s in all_subjects().values() if s.name == "Технология разработки программного обеспечения")
    assert subj.short_name == "ТРПО" and subj.kind == "code"
    p = ctx.store.get_profile(PID)
    e = p.enrollment(subj.id)
    assert e and e.teacher == "С.С. Сидоров" and e.variant == 5 and e.header_style == "short"
    assert p.enrollment("psip") is not None  # отмеченные раньше предметы на месте
    assert "Технология разработки программного обеспечения" in c.get("/labs/new").text
    # тот же предмет второй раз — не дубль, а отметка существующего
    c.post("/profiles/save", data=_form(new_subject_name="технология разработки программного обеспечения"))
    assert sum(1 for s in all_subjects().values() if s.name.lower().startswith("технология разработки")) == 1
