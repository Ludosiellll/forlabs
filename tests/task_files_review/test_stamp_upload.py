"""Загрузка картинки-метки: поворот по EXIF, расход памяти, и что страница лабы говорит о файлах заданий."""

import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_routes import FIXTURE, RAW, make_profile

from forlabs.core.llm import FakeLLM
from forlabs.core.settings import Settings
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

P, S = "ivanov_ivan_petrovich", "psip"


@pytest.fixture
def app(tmp_home, tmp_path):
    fake = FakeLLM([RAW, RAW, RAW])
    ctx = AppContext(settings=Settings(mode="local"), llm_override=lambda: fake, prefer_word=False,
                     render_diagrams=False, synchronous=True)
    client = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(client, tmp_path)
    return client, ctx


def _stored(ctx) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(ctx.store.get_profile(P).stamp))).convert("RGBA")


def test_phone_photo_is_turned_by_its_exif_orientation(app):
    """Фото с телефона (JPEG, поворот записан в EXIF) встаёт в документы лёжа на боку: остальные загрузки картинок
    в ForLabs (prepare_image, normalize_screenshot) поворот учитывают, загрузка метки — нет."""
    client, ctx = app
    im = Image.new("RGB", (400, 200), (255, 255, 255))
    im.paste((200, 0, 0), (0, 0, 200, 200))  # левая половина кадра красная …
    im.paste((0, 0, 200), (200, 0, 400, 200))  # … правая синяя
    exif = Image.Exif()
    exif[0x0112] = 6  # телефон держали вертикально: показывать, повернув на 90° по часовой
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif, quality=95)
    r = client.post(f"/profiles/{P}/stamp", files={"stamp": ("me.jpg", buf.getvalue(), "image/jpeg")},
                    follow_redirects=False)
    assert "err=" not in r.headers["location"]
    box = _stored(ctx).getbbox()  # непрозрачная часть квадрата — сама картинка
    assert (box[3] - box[1]) > (box[2] - box[0]), f"метка лежит на боку: {box}"


def test_narrow_picture_does_not_allocate_a_huge_square(app, monkeypatch):
    """Картинка приводится к квадрату ДО уменьшения: узкая полоска 100×8000 (0,8 Мп, файл в пару килобайт) требует
    холст 8000×8000 RGBA — 256 МБ; длинный снимок экрана 1080×20000 — 1,6 ГБ. Остальные загрузки отказывают
    картинкам больше 40 Мп (images.MAX_PIXELS), эта проверки не делает."""
    client, ctx = app
    made: list[int] = []
    real = Image.new

    def new(mode, size, *a, **kw):
        made.append(size[0] * size[1])
        return real(mode, size, *a, **kw)

    buf = io.BytesIO()
    real("RGB", (100, 8000), (10, 120, 60)).save(buf, "PNG")
    monkeypatch.setattr(Image, "new", new)
    r = client.post(f"/profiles/{P}/stamp", files={"stamp": ("strip.png", buf.getvalue(), "image/png")},
                    follow_redirects=False)
    assert "err=" not in r.headers["location"]
    assert max(made) <= 40_000_000, f"холст {max(made) / 1e6:.0f} Мп ради метки 300×300"


def test_lab_page_does_not_list_task_files_that_are_not_in_the_folder(app):
    """Страница лабы показывает «Файлы заданий в папке сдачи: …» прямо из st.task_docs, не глядя в папку. Лаба
    открыта на другом компьютере (состояние из облака) или студент убрал папку — файлов нет, а страница говорит,
    что они лежат. Чат («Где что лежит») в этом случае проверяет диск, страница — нет."""
    client, ctx = app
    url = client.post("/labs/new", data={"profile_id": P, "subject_id": S, "number": "", "text": "",
                                         "task_files": "1"},
                      files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")},
                      follow_redirects=False).headers["location"]
    st = ctx.store.load_lab(P, S, "7")
    st.task_docs = ["Задание 1 — Анимации страницы.docx"]  # как в состоянии, пришедшем с другого компьютера
    ctx.store.save_lab(st)
    assert not (ctx.pipeline._hand_in_dir(st) / st.task_docs[0]).exists()
    page = client.get(url).text
    assert "Задание 1 — Анимации страницы.docx" not in page
