"""Облако без интернета: страницы открываются из копии на этом компьютере, с понятной плашкой."""

from test_admin import fake, make_client  # noqa: F401 — фикстура fake

from forlabs.core.models import LabState, Profile


def test_lab_opens_offline_with_banner(fake, tmp_home):  # noqa: F811
    client = make_client(fake, tmp_home, me="member")
    store = client.app.state.ctx.store
    store.retry_delays = (0, 0)
    store.save_profile(Profile(id="ivanov", full_name="Иванов Иван", title_name="И.И. Иванов"))
    store.save_lab(LabState(profile_id="ivanov", subject_id="ssp", number="4", status="rendered"))
    fake.drop_next = 10_000  # интернет пропал
    page = client.get("/labs/ivanov/ssp/4?tab=report")
    assert page.status_code == 200 and "Нет связи с облаком" in page.text
    home = client.get("/?profile=ivanov&subject=ssp")
    assert home.status_code == 200 and "Нет связи с облаком" in home.text


def test_home_does_not_download_full_lab_states(fake, tmp_home):  # noqa: F811
    from forlabs.core.models import Enrollment

    client = make_client(fake, tmp_home, me="member")
    store = client.app.state.ctx.store
    store.save_profile(Profile(id="ivanov", full_name="Иванов Иван", title_name="И.И. Иванов",
                               enrollments=[Enrollment(subject_id="ssp", title_subject="ССП"),
                                            Enrollment(subject_id="ks", title_subject="КС")]))
    st = LabState(profile_id="ivanov", subject_id="ssp", number="4", status="rendered")
    store.save_lab(st)
    fake.requests.clear()
    page = client.get("/?profile=ivanov&subject=ssp").text
    assert "ЛР 4" in page
    lab_selects = [r.url.params.get("select", "") for r in fake.requests
                   if r.url.path == "/rest/v1/labs" and r.method == "GET"]
    assert lab_selects and all(s != "state" for s in lab_selects), lab_selects
