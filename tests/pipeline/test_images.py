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
