"""Аудит-2, интерфейс: заголовки защиты, предметы, требования, загрузки, ошибки вставки, безопасные имена."""

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from forlabs.core.llm import FakeLLM
from forlabs.core.settings import Settings
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

TEMPLATES = Path(__file__).parents[2] / "forlabs" / "ui" / "templates"
STATIC = Path(__file__).parents[2] / "forlabs" / "ui" / "static"


@pytest.fixture
def ctx(tmp_home):
    tmp_home.mkdir(parents=True, exist_ok=True)
    return AppContext(settings=Settings(mode="local"), llm_override=lambda: FakeLLM([]), prefer_word=False,
                      render_diagrams=False, synchronous=True)


@pytest.fixture
def client(ctx):
    return TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")


# F2: чужой сайт не может встроить окно ForLabs во фрейм и подсунуть клик
def test_frame_and_sniff_headers(client):
    r = client.get("/health")
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


# F5: новый предмет с тем же коротким именем не заменяет существующий
def test_subject_collision_keeps_existing(client):
    from forlabs.core.subjects import get_subject

    before = get_subject("ks").name
    client.post("/subjects/add", data={"name": "Компьютерная схемотехника", "short_name": "КС"})
    assert get_subject("ks").name == before
    assert get_subject("ks-2").name == "Компьютерная схемотехника"


# F7: двойной клик «убрать» убирает одно требование, а не два
def test_teacher_rule_removed_by_key(ctx, client):
    from forlabs.pipeline.memory import teacher_key

    mem = ctx.memory()
    for r in ("Комментарии к методам", "Таблицы с рамкой", "Вывод в одно предложение"):
        mem.add_teacher("ivan", "psip", r)
    key = teacher_key(mem.teacher_list("ivan", "psip")[1])
    for _ in range(2):
        client.post(f"/labs/ivan/psip/1/teacher/remove/{key}", follow_redirects=False)
    assert [x.rstrip(".") for x in mem.teacher_list("ivan", "psip")] == ["Комментарии к методам",
                                                                         "Вывод в одно предложение"]


# F9: огромный файл не съедает память приложения
def test_upload_size_limit(client, monkeypatch):
    from forlabs.ui import app as app_mod

    monkeypatch.setattr(app_mod, "MAX_UPLOAD", 1000)
    r = client.post("/labs/new", data={"profile_id": "p", "subject_id": "psip"},
                    files={"files": ("big.pdf", b"x" * 5000, "application/pdf")}, follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]


# F10: картинка-«бомба» отклоняется до распаковки
def test_huge_image_rejected_before_decoding():
    from forlabs.pipeline.images import normalize_screenshot, prepare_image

    buf = io.BytesIO()
    Image.new("1", (13000, 13000)).save(buf, format="PNG")
    with pytest.raises(ValueError):
        prepare_image(buf.getvalue())
    with pytest.raises(ValueError):
        normalize_screenshot(buf.getvalue())


# F13: ошибка вставки скриншота — JSON с причиной, а не страница
def test_screenshot_error_is_json(ctx, client, monkeypatch):
    from forlabs.cloud.auth import CloudError

    def offline(*a, **k):
        raise CloudError("Нет связи с облаком. Проверь интернет.", offline=True)

    monkeypatch.setattr(ctx.store, "load_lab", offline)
    r = client.post("/labs/p/s/1/screenshot/1/1", content=b"png", headers={"Content-Type": "image/png"})
    assert r.headers["content-type"].startswith("application/json")
    assert "Нет связи" in r.json()["error"]


# F17, F12, F14
def test_templates_texts_and_once_forms():
    assert "«Админ»" in (TEMPLATES / "pending.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "pageshow" in js and "form[method=post]" in js.lower()
    css = "".join(p.read_text(encoding="utf-8") for p in STATIC.glob("*.css"))
    assert "overflow-wrap" in css


# F18: имена папок без «..», зарезервированных имён Windows и точек на конце
@pytest.mark.parametrize("raw", ["..", ".", "CON", "prn", "com1", "тема.", "a. "])
def test_safe_name_windows(raw):
    from forlabs.core.store import safe_name

    s = safe_name(raw)
    assert s not in ("..", ".") and not s.endswith((".", " "))
    assert s.split(".")[0].upper() not in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}


# D5: настройки были повреждены — объяснение, а не молчаливый переход в облако
def test_broken_settings_notice(tmp_home):
    from forlabs.core import settings as settings_mod

    tmp_home.mkdir(parents=True, exist_ok=True)
    settings_mod._settings_file().write_text('{"mode": "lo', encoding="utf-8")
    ctx = AppContext(synchronous=True)
    assert ctx.notice and "Настройки" in ctx.notice


# D6: ошибка при создании лабы не оставляет форму «уже создаётся»
def test_form_token_released_on_error(ctx, client, monkeypatch):
    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ctx.pipeline, "create_lab", boom)
    data = {"profile_id": "p", "subject_id": "psip", "text": "методичка", "form_token": "tok1"}
    quiet = TestClient(client.app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False)
    r = quiet.post("/labs/new", data=data, follow_redirects=False)
    assert "место" in r.text
    assert ctx.form_tokens.get("tok1") is None


# D7: свои правила пишутся атомарно
def test_rule_override_atomic(tmp_home, monkeypatch):
    from forlabs.core import rules

    written = []
    monkeypatch.setattr(rules, "atomic_write_text", lambda f, t: written.append(f))
    tmp_home.mkdir(parents=True, exist_ok=True)
    rules._default_save("common/standard.md", "текст")
    assert written


# F1: отчёт за семестр сохраняется в папку сдачи, путь показывается
def test_semester_saved_to_output_folder(ctx, client, tmp_path, monkeypatch):
    from forlabs.core.models import Profile

    out_root = tmp_path / "сдача"
    ctx.store.save_profile(Profile(id="ivan", full_name="Иванов Иван", title_name="И. Иванов",
                                   output_root=str(out_root)))

    def fake_build(pid, sid, numbers, out):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"docx")
        return Path(out), None

    monkeypatch.setattr(ctx.pipeline, "build_semester", fake_build)
    r = client.post("/semester", data={"profile_id": "ivan", "subject_id": "psip", "numbers": ["1"]},
                    follow_redirects=False)
    saved = list(out_root.rglob("*.docx"))
    assert saved and r.status_code == 303 and "msg=" in r.headers["location"]
