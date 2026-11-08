"""Важные находки аудита: страница ошибки, длинные имена, сдача, двойной клик, ответы модели."""

import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from forlabs.core.llm import LLM, FakeLLM, LLMError
from forlabs.core.settings import Settings
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

FIXTURE = Path(__file__).parents[1] / "fixtures" / "methodology_psip_lr7.txt"


def test_unexpected_error_shows_a_page_not_bare_500(tmp_home):
    ctx = AppContext(settings=Settings(mode="local"), synchronous=True)
    client = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765", raise_server_exceptions=False)

    def boom():
        raise RuntimeError("что-то внутри сломалось")

    ctx.store.list_profiles = boom
    r = client.get("/")
    assert r.status_code == 500 and "Что-то пошло не так" in r.text and 'href="/"' in r.text


def test_long_file_name_is_shortened(tmp_home, tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "ui"))
    from test_routes import RAW, make_profile

    ctx = AppContext(settings=Settings(mode="local"), llm_override=lambda: FakeLLM([RAW]), prefer_word=False,
                     render_diagrams=False, synchronous=True)
    client = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(client, tmp_path)
    name = "Методические_указания_к_лабораторной_работе_" * 8 + ".txt"
    r = client.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                       "text": ""}, files={"files": (name, FIXTURE.read_bytes(), "text/plain")},
                    follow_redirects=False)
    assert r.status_code == 303 and "err=" not in r.headers["location"]
    files = list((ctx.store.lab_dir("ivanov_ivan_petrovich", "psip", "7") / "source").iterdir())
    assert len(files) == 1 and len(files[0].name) <= 100 and files[0].suffix == ".txt"


def test_unreachable_submission_folder_is_a_message(tmp_home, tmp_path):
    sys_path = str(Path(__file__).parents[1] / "pipeline")
    import sys

    sys.path.insert(0, sys_path)
    from test_run import ANS, RAW, SEC, SEC2, png_bytes, setup

    from forlabs.pipeline.run import PipelineError

    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    blocker = tmp_path / "флешка"
    blocker.write_text("это файл, а не папка")  # как отключённая флешка или недоступный диск
    p = pipe.store.get_profile("ivanov")
    pipe.store.save_profile(p.model_copy(update={"output_root": str(blocker / "Лабы")}))
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    with pytest.raises(PipelineError) as e:
        pipe.mark_final(st)
    assert "Папка сдачи недоступна" in str(e.value)


def test_final_does_not_wait_for_memory_update(tmp_home, tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "pipeline"))
    from test_run import ANS, RAW, SEC, SEC2, png_bytes, setup

    from forlabs.pipeline.memory import SubjectMemory

    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS])
    pipe.memory = SubjectMemory()
    pipe.memory.update = lambda st, llm: time.sleep(3)
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    t0 = time.monotonic()
    st = pipe.mark_final(st)
    assert time.monotonic() - t0 < 2 and pipe.store.load_lab("ivanov", "psip", "7").status == "final"
    target = tmp_path / "сдача" / "ПССИП" / "ЛР7"
    assert [f.name for f in target.iterdir()] == ["ЛР7_ПССИП.docx"]  # без недописанных .tmp


def test_double_submit_creates_one_lab(tmp_home, tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "ui"))
    from test_routes import RAW, make_profile

    ctx = AppContext(settings=Settings(mode="local"), llm_override=lambda: FakeLLM([RAW, RAW]), prefer_word=False,
                     render_diagrams=False, synchronous=True)
    client = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(client, tmp_path)
    page = client.get("/labs/new").text
    assert 'name="form_token"' in page
    token = page.split('name="form_token" value="')[1].split('"')[0]
    data = {"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "", "text": "", "form_token": token}
    r1 = client.post("/labs/new", data=data, files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")},
                     follow_redirects=False)
    r2 = client.post("/labs/new", data=data, files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")},
                     follow_redirects=False)
    assert r1.headers["location"] == r2.headers["location"]
    assert [s.number for s in ctx.store.list_labs("ivanov_ivan_petrovich", "psip")] == ["7"]


class Blocks(__import__("pydantic").BaseModel):
    blocks: list = []


def _completion(content, finish="stop"):
    return {"id": "x", "object": "chat.completion", "model": "m",
            "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _llm(handler):
    return LLM(Settings(base_url="https://example.test/v1"), api_key="k",
               http_client=httpx.Client(transport=httpx.MockTransport(handler)), retry_delays=(0, 0))


def test_cut_off_answer_is_not_paid_four_times():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=_completion('{"blocks": [{"type": "paragraph", "text": "обрез', "length"))

    with pytest.raises(LLMError) as e:
        _llm(handler).json([{"role": "user", "content": "x"}], Blocks)
    assert len(calls) == 1 and "не поместился" in e.value.message_ru


def test_too_big_methodology_is_explained():
    def handler(req):
        return httpx.Response(400, json={"error": {"message": "This model's maximum context length is 131072 tokens"}})

    with pytest.raises(LLMError) as e:
        _llm(handler).chat([{"role": "user", "content": "x"}])
    assert "слишком большая" in e.value.message_ru
