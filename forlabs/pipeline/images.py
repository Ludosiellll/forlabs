"""Фото к пожеланиям и к сообщениям чата: уменьшаем, сохраняем и даём модели «прочитать» (DeepSeek v4.1 flash
умеет картинки). Прочитанный текст показывается студенту и дальше учитывается как обычный текст."""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 1600
MAX_BYTES = 15 * 1024 * 1024
MAX_IMAGES = 4

DESCRIBE_SYSTEM = """Студент прикладывает фото к лабораторной работе: замечания или задание преподавателя, запись на
доске или в тетради, страницу методички, скриншот ошибки или результата. Перепиши весь текст с фото дословно,
рукописный тоже. Если на фото код, схема, таблица или интерфейс программы — коротко опиши то, что важно для отчёта.
Каждое фото — с новой строки «Фото N: …». Без вступлений и выводов, по-русски."""


MAX_PIXELS = 40_000_000  # больше — подозрительно (картинка-«бомба» распакуется в гигабайты памяти)


def _check_size(im) -> None:
    w, h = im.size
    if w * h > MAX_PIXELS:
        raise ValueError(f"Картинка {w}×{h} точек — слишком большая. Уменьши её или сделай снимок части экрана.")


def prepare_image(data: bytes) -> bytes:
    """Проверяет, что это картинка, поворачивает по EXIF, уменьшает до MAX_SIDE и сохраняет в JPEG."""
    if len(data) > MAX_BYTES:
        raise ValueError("Фото больше 15 МБ")
    try:
        with Image.open(io.BytesIO(data)) as im:
            _check_size(im)  # размер из заголовка — до распаковки
            im.draft("RGB", (MAX_SIDE, MAX_SIDE))
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=85, optimize=True)
            return out.getvalue()
    except Image.DecompressionBombError as e:
        raise ValueError("Фото слишком большое по размеру в пикселях — уменьши его") from e
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError("Это не картинка: нужен снимок PNG или JPG") from e


def image_part(path: Path) -> dict:
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}


def describe_images(paths: list[Path], llm) -> str:
    """Один запрос к модели на все фото сообщения."""
    content = [{"type": "text", "text": f"Фото: {len(paths)} шт. Перепиши и опиши их."}]
    content += [image_part(p) for p in paths]
    return llm.chat([{"role": "system", "content": DESCRIBE_SYSTEM}, {"role": "user", "content": content}],
                    temperature=0.1).strip()


def normalize_screenshot(data: bytes) -> tuple[bytes, str]:
    """Скриншот для отчёта: любой формат, который читает Pillow (PNG, JPG, WebP, BMP…) → PNG или JPG.
    Word понимает только их, а WebP или битый файл раньше навсегда ломал вёрстку."""
    if len(data) > MAX_BYTES:
        raise ValueError("Снимок больше 15 МБ")
    try:
        with Image.open(io.BytesIO(data)) as im:
            _check_size(im)
            fmt = im.format
            im = ImageOps.exif_transpose(im)
            im.thumbnail((4000, 4000))
            out = io.BytesIO()
            if fmt == "JPEG":
                im.convert("RGB").save(out, format="JPEG", quality=92)
                return out.getvalue(), ".jpg"
            im.save(out, format="PNG", optimize=True)
            return out.getvalue(), ".png"
    except Image.DecompressionBombError as e:
        raise ValueError("Снимок слишком большой по размеру в пикселях") from e
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ValueError("Это не картинка — вставь снимок экрана (PNG или JPG)") from e


def image_ok(path: Path) -> bool:
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            im.load()
        return True
    except Exception:
        return False
