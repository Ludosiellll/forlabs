"""Фото к пожеланиям и к сообщениям чата: подготовка и «чтение» моделью."""

import io

import pytest
from PIL import Image

from forlabs.core.llm import FakeLLM
from forlabs.pipeline.images import MAX_SIDE, describe_images, image_part, prepare_image


def png(w=3000, h=2000, color=(250, 250, 250)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def test_prepare_image_downscales_to_jpeg():
    out = prepare_image(png())
    with Image.open(io.BytesIO(out)) as im:
        assert im.format == "JPEG" and max(im.size) == MAX_SIDE


def test_prepare_image_rejects_non_images():
    with pytest.raises(ValueError):
        prepare_image(b"%PDF-1.4 not an image")


def test_describe_images_sends_image_parts(tmp_path):
    f = tmp_path / "note.jpg"
    f.write_bytes(prepare_image(png(400, 200)))
    fake = FakeLLM(["Фото 1: Вариант 7, цикл while."])
    text = describe_images([f], fake)
    assert text == "Фото 1: Вариант 7, цикл while."
    content = fake.calls[0][-1]["content"]
    assert isinstance(content, list) and content[1] == image_part(f)
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_decompression_bomb_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    with pytest.raises(ValueError):
        prepare_image(png(400, 400))


# --- картинки внутри методички -------------------------------------------------------------------------------------

from forlabs.core.llm import LLMError  # noqa: E402
from forlabs.ingest.extract import Picture, picture_mark  # noqa: E402
from forlabs.pipeline.images import (  # noqa: E402
    MAX_SOURCE_PICTURES,
    SOURCE_BATCH,
    UNREAD_PICTURE,
    put_pictures,
    read_source_pictures,
)


class SlowEcho:
    """Модель, которая отвечает по содержимому запроса и запоминает, сколько запросов шло одновременно."""

    def __init__(self):
        import threading

        self.lock, self.now, self.peak, self.calls = threading.Lock(), 0, 0, 0

    def json(self, messages, schema, repairs=3, temperature=None, max_tokens=None):
        import time

        with self.lock:
            self.now += 1
            self.calls += 1
            self.peak = max(self.peak, self.now)
        time.sleep(0.05)
        parts = [c for c in messages[-1]["content"] if c["type"] == "image_url"]
        with self.lock:
            self.now -= 1
        return schema.model_validate({"pictures": [f"текст {len(p['image_url']['url'])}" for p in parts]})


def pics(n, page=False):
    return [Picture(png(400, 200, (200, 200, 200 - i)), page=page) for i in range(n)]


def test_source_pictures_are_read_in_one_request_per_batch():
    fake = FakeLLM([{"pictures": ["SELECT * FROM Products LIMIT 3;", "Таблица Products: Id | Name"]}])
    texts, error = read_source_pictures(pics(2), fake)
    assert texts == ["SELECT * FROM Products LIMIT 3;", "Таблица Products: Id | Name"] and error == ""
    content = fake.calls[0][-1]["content"]
    assert [c["type"] for c in content] == ["text", "image_url", "image_url"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_source_pictures_known_ones_are_not_paid_twice():
    known = {}
    fake = FakeLLM([{"pictures": ["первая", "вторая"]}])
    read_source_pictures(pics(2), fake, known)
    assert len(known) == 2
    again = FakeLLM([])  # ни одного запроса: всё уже прочитано
    texts, _ = read_source_pictures(pics(2), again, known)
    assert texts == ["первая", "вторая"] and again.calls == []


def test_source_pictures_wrong_count_is_asked_one_by_one():
    fake = FakeLLM([{"pictures": ["всё в одной строке"]}, {"pictures": ["первая"]}, {"pictures": ["вторая"]}])
    texts, _ = read_source_pictures(pics(2), fake)
    assert texts == ["первая", "вторая"] and len(fake.calls) == 3


def test_source_pictures_failure_leaves_them_unread_with_a_reason():
    fake = FakeLLM([LLMError("network", "timeout")])
    texts, error = read_source_pictures(pics(3), fake)
    assert texts == [None, None, None] and error
    assert len(fake.calls) == 1  # после сбоя связи остальные пачки не отправляются


def test_source_pictures_over_the_limit_stay_unread():
    n = MAX_SOURCE_PICTURES + 2
    llm = SlowEcho()  # отвечает по содержимому запроса: порядок одновременных пачек не важен
    texts, error = read_source_pictures(pics(n), llm)
    assert all(texts[:MAX_SOURCE_PICTURES]) and llm.calls == MAX_SOURCE_PICTURES // SOURCE_BATCH
    assert texts[MAX_SOURCE_PICTURES:] == [None, None] and "больше" in error


def test_source_picture_that_is_not_an_image_is_unread():
    texts, _ = read_source_pictures([Picture(b"\x01\x00\x00\x00 emf"), *pics(1)], FakeLLM([{"pictures": ["текст"]}]))
    assert texts == [None, "текст"]


def test_put_pictures_wraps_inline_ones_and_inlines_pages():
    text = f"Задание 1. Выполните запросы.\n{picture_mark(1)}\nЗадание 2\n{picture_mark(2)}\n{picture_mark(3)}\nКонец"
    pictures = [Picture(b"a"), Picture(b"b", page=True), Picture(b"c")]
    out = put_pictures(text, pictures, ["SELECT 1;\nSELECT 2;", "Текст страницы целиком", None])
    assert "[Картинка в методичке:\nSELECT 1;\nSELECT 2;\n]" in out
    assert "\nТекст страницы целиком\n" in out and "[Картинка в методичке: Текст страницы" not in out
    assert UNREAD_PICTURE in out
    assert "[[картинка" not in out


def test_put_pictures_drops_decorative_ones():
    out = put_pictures(f"Тема\n{picture_mark(1)}\nЦель", [Picture(b"a")], [""])
    assert out == "Тема\nЦель"


def test_source_picture_batches_go_to_the_model_at_once():
    # десять картинок — три пачки: ждать их по очереди втрое дольше
    many = [Picture(png(300 + 10 * i, 200, (200, 200, 200 - i))) for i in range(10)]
    llm = SlowEcho()
    texts, error = read_source_pictures(many, llm)
    assert error == "" and all(t and t.startswith("текст ") for t in texts)
    assert llm.calls == 3 and llm.peak >= 2
    assert len(set(texts)) > 1  # тексты разошлись по своим картинкам, а не перепутались
