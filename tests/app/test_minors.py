"""Мелкие сбои из аудита устойчивости: часы, выключение во время записи, зависания, понятные тексты."""

import errno
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "cloud"))
from test_cloud import CFG, FakeSupabase  # noqa: E402

from forlabs.cloud.auth import CloudAuth  # noqa: E402
from forlabs.cloud.store import CloudStore  # noqa: E402
from forlabs.core.llm import LLM, LLMError  # noqa: E402
from forlabs.core.settings import Settings  # noqa: E402


def cloud(tmp_home, fake=None):
    fake = fake or FakeSupabase()
    auth = CloudAuth(CFG, http=httpx.Client(transport=httpx.MockTransport(fake)), use_keyring=False)
    auth.sign_in("a@b.c", "верный")
    return fake, auth, CloudStore(auth, retry_delays=(0, 0))


# M1: часы ноутбука спешат/отстают — сервер говорит «JWT expired», а приложение считает токен свежим
def test_expired_token_is_refreshed_once(tmp_home):
    fake, auth, store = cloud(tmp_home)
    fake.expired.add(auth.session.access_token)
    assert store.list_members()  # обновил вход и повторил
    assert auth.session.access_token not in fake.expired


# M2: обрыв связи как раз в момент обновления входа — повторяется, как и обычные запросы
def test_refresh_network_blip_is_retried(tmp_home):
    fake, auth, store = cloud(tmp_home)
    auth.session = auth.session.model_copy(update={"expires_at": int(time.time()) + 10})  # пора обновлять
    fake.drop_auth_next = 1
    assert store.list_members()


# M3: выход на этом компьютере не выкидывает из аккаунта на других
def test_sign_out_is_local(tmp_home):
    fake, auth, _ = cloud(tmp_home)
    auth.sign_out()
    logout = [r for r in fake.requests if r.url.path == "/auth/v1/logout"]
    assert logout and logout[0].url.params.get("scope") == "local"


# M6: ноутбук выключился во время записи отчёта — прежний файл цел
def test_docx_save_keeps_old_file_when_interrupted(tmp_path, monkeypatch):
    from docx import Document

    from forlabs.render import docx_builder

    out = tmp_path / "report.docx"
    out.write_bytes(b"old")
    doc = Document()

    def broken_save(path):
        Path(path).write_bytes(b"PK\x03\x04half")
        raise OSError(errno.EIO, "power lost")

    monkeypatch.setattr(doc, "save", broken_save)
    with pytest.raises(OSError):
        docx_builder._save(doc, out, "Автор")
    assert out.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["report.docx"]


# M9: ожидание событий не зависит от перевода часов (сон/пробуждение ноутбука)
def test_event_wait_uses_monotonic_clock(monkeypatch):
    from forlabs.pipeline import events

    bus = events.EventBus()
    monkeypatch.setattr(events.time, "time", lambda: (_ for _ in ()).throw(AssertionError("time.time")))
    assert bus.wait("k", 0, timeout=0.01) == []


def _script(tmp_path: Path, body: str) -> str:
    exe = tmp_path / "fake.sh"
    exe.write_text("#!/bin/sh\n" + body + "\n")
    exe.chmod(0o755)
    return str(exe)


# M4: старый PDF от прошлой сборки не выдаётся за новый
@pytest.mark.skipif(sys.platform == "win32", reason="скрипт-заглушка sh")
def test_libreoffice_stale_pdf_is_not_reused(tmp_path):
    from forlabs.render.libreoffice import to_pdf

    docx = tmp_path / "report.docx"
    docx.write_bytes(b"x")
    out = tmp_path / "render"
    out.mkdir()
    (out / "report.pdf").write_bytes(b"%PDF old")
    with pytest.raises(RuntimeError):
        to_pdf(docx, out, _script(tmp_path, "exit 0"))


@pytest.mark.skipif(sys.platform == "win32", reason="скрипт-заглушка sh")
def test_libreoffice_hang_is_explained(tmp_path):
    from forlabs.render.libreoffice import to_pdf

    docx = tmp_path / "report.docx"
    docx.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="слишком долго"):
        to_pdf(docx, tmp_path / "render", _script(tmp_path, "sleep 5"), timeout=0.5)


# M5: предпросмотр не пропадает, если новая сборка страниц не удалась
def test_preview_keeps_old_pages_on_failure(tmp_path, monkeypatch):
    from forlabs.render import preview

    out = tmp_path / "pages"
    out.mkdir()
    for i in (1, 2, 3):
        (out / f"page-{i:03d}.png").write_bytes(b"old")
    monkeypatch.setattr(preview.pymupdf, "open", lambda p: (_ for _ in ()).throw(RuntimeError("битый PDF")))
    with pytest.raises(RuntimeError):
        preview.pdf_to_pngs(tmp_path / "x.pdf", out)
    assert len(list(out.glob("page-*.png"))) == 3


def test_preview_removes_extra_old_pages(tmp_path):
    import pymupdf

    from forlabs.render import preview

    pdf = tmp_path / "one.pdf"
    with pymupdf.open() as d:
        d.new_page()
        d.save(pdf)
    out = tmp_path / "pages"
    out.mkdir()
    for i in (1, 2, 3):
        (out / f"page-{i:03d}.png").write_bytes(b"old")
    assert [p.name for p in preview.pdf_to_pngs(pdf, out)] == ["page-001.png"]
    assert sorted(p.name for p in out.glob("page-*.png")) == ["page-001.png"]


# M8: Tesseract завис на картинке — не вечное ожидание
@pytest.mark.skipif(sys.platform == "win32", reason="скрипт-заглушка sh")
def test_tesseract_hang_is_explained(tmp_path, monkeypatch):
    from forlabs.ingest import extract

    monkeypatch.setattr(extract, "OCR_TIMEOUT", 0.5)
    img = tmp_path / "page.png"
    img.write_bytes(b"x")
    res = extract._image(img, _script(tmp_path, "sleep 5"))
    assert res.text == "" and "слишком долго" in res.warnings[0]


# M12: «слишком много запросов» — ждём, сколько просит сервер, и пишем спокойный текст
def test_rate_limit_honours_retry_after(monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(429, headers={"Retry-After": "7"}, json={"error": {"message": "rate limit"}})

    slept = []
    monkeypatch.setattr("forlabs.core.llm.time.sleep", slept.append)
    llm = LLM(Settings(), api_key="k", http_client=httpx.Client(transport=httpx.MockTransport(handler)),
              retry_delays=(1.0,))
    with pytest.raises(LLMError) as e:
        llm.chat([{"role": "user", "content": "да"}])
    assert slept == [7.0] and len(calls) == 2
    assert "баланс" not in e.value.message_ru and "подожди" in e.value.message_ru.lower()


def _pipe(tmp_home):
    from forlabs.core.llm import FakeLLM
    from forlabs.core.store import Store
    from forlabs.pipeline.run import Pipeline

    tmp_home.mkdir(parents=True, exist_ok=True)
    pipe = Pipeline(Store(), llm_factory=lambda: FakeLLM([]), prefer_word=False, render_diagrams=False)
    return pipe, pipe.create_lab("ivanov", "ssp", [("m.txt", b"methodology")], number="1")


# M11: журнал лабы не растёт бесконечно
def test_journal_is_capped(tmp_home):
    from forlabs.pipeline import run

    pipe, st = _pipe(tmp_home)
    f = pipe.lab_dir(st) / "journal.log"
    f.write_text("info\tстарое\n" * (run.JOURNAL_MAX // 10), encoding="utf-8")  # почти вдвое больше предела
    pipe._emit(st, "новое событие")
    assert f.stat().st_size <= run.JOURNAL_MAX
    assert f.read_text(encoding="utf-8").splitlines()[-1].endswith("новое событие")


# M14: ошибки файловой системы — по-русски и с именем файла
def test_os_errors_are_explained_in_russian(tmp_home):
    pipe, st = _pipe(tmp_home)
    st = pipe._fail(st, PermissionError(errno.EACCES, "Permission denied", "C:/Users/a/report.docx"))
    assert "Нет доступа" in st.error and "report.docx" in st.error
    st = pipe._fail(st, OSError(errno.ENOSPC, "No space left on device"))
    assert "место" in st.error


# M7: «Админ» без интернета — не молчаливый уход на главную, а объяснение
def test_admin_offline_is_explained(tmp_home):
    from fastapi.testclient import TestClient

    from forlabs.ui.app import create_app
    from forlabs.ui.context import AppContext

    fake, auth, store = cloud(tmp_home)
    ctx = AppContext(settings=Settings(mode="cloud"), synchronous=True)
    ctx.auth, ctx._store = auth, store
    client = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    fake.drop_next = 100
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert "Нет связи" in httpx.URL(r.headers["location"]).params["err"]


def _local_client(tmp_home):
    from fastapi.testclient import TestClient

    from forlabs.ui.app import create_app
    from forlabs.ui.context import AppContext

    tmp_home.mkdir(parents=True, exist_ok=True)
    ctx = AppContext(settings=Settings(mode="local"), synchronous=True)
    return ctx, TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")


# M10: выход из аккаунта или смена настроек посреди работы над лабой
def test_settings_and_logout_wait_for_running_lab(tmp_home):
    ctx, client = _local_client(tmp_home)
    ctx.running.add("ivanov/ssp/1")
    before = ctx.settings.model
    r = client.post("/settings", data={"model": "deepseek-v4-flash"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and ctx.settings.model == before
    r = client.post("/logout", follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_finished_job_reports_moved_lab_after_reset(tmp_home):
    ctx, _ = _local_client(tmp_home)
    pipe = ctx.pipeline

    def job():
        pipe.renamed["ivanov/ssp/draft-1"] = "4"
        ctx.reset()  # например, сохранили правила, пока шла работа

    ctx.run("ivanov/ssp/draft-1", job)
    assert [e.kind for e in ctx.bus.history("ivanov/ssp/4")] == ["idle"]
