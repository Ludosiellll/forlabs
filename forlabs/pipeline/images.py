"""Фото к пожеланиям и к сообщениям чата: уменьшаем, сохраняем и даём модели «прочитать» (DeepSeek v4.1 flash
умеет картинки). Прочитанный текст показывается студенту и дальше учитывается как обычный текст."""

from __future__ import annotations

import base64
import hashlib
import io
import re
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, field_validator

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


MAX_SOURCE_PICTURES = 16  # новых картинок за один разбор: дальше дорого, остаток помечается непрочитанным
SOURCE_BATCH = 4
SOURCE_WORKERS = 3  # пачек одновременно
UNREAD_PICTURE = "[Картинка в методичке — прочитать не удалось]"

SOURCE_SYSTEM = """Тебе дают картинки из методички лабораторной работы колледжа: снимки кода, запросов и команд,
таблицы, схемы, окна программ, страницы целиком. Для каждой картинки верни её содержание текстом:
- текст, код, запросы и команды перепиши дословно и полностью, построчно, с теми же именами, числами и знаками;
  ничего не исправляй, не сокращай и не дополняй;
- таблицу перепиши по строкам, ячейки через « | »;
- окно программы, форму, схему или диаграмму опиши коротко и точно: что это и какие на ней элементы, поля, кнопки,
  подписи, значения и связи (у схемы базы данных — таблицы, их поля, ключи и связи);
- если на картинке нет ничего нужного для выполнения работы (эмблема, украшение, фотография без текста) —
  пустая строка.
Задание не решай и от себя ничего не добавляй. Ответ — только JSON {"pictures": ["содержание первой",
"содержание второй", …]}: столько элементов, сколько картинок, в том же порядке."""


class SourcePictures(BaseModel):
    pictures: list[str] = []

    @field_validator("pictures", mode="before")
    @classmethod
    def _strings(cls, v):
        return ["" if x is None else str(x) for x in v] if isinstance(v, list) else []


def _jpeg_part(jpeg: bytes) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")}}


def _ask_pictures(jpegs: list[bytes], llm) -> list[str] | None:
    """Содержание картинок по порядку; None — модель ответила не на все (или лишнее)."""
    content = [{"type": "text", "text": f"Картинок: {len(jpegs)}. Верни содержание каждой."}]
    content += [_jpeg_part(j) for j in jpegs]
    got: SourcePictures = llm.json([{"role": "system", "content": SOURCE_SYSTEM}, {"role": "user", "content": content}],
                                   SourcePictures, repairs=1, temperature=0.0)
    return got.pictures if len(got.pictures) == len(jpegs) else None


def read_source_pictures(pictures: list, llm, known: dict[str, str] | None = None) -> tuple[list[str | None], str]:
    """Текст каждой картинки методички (None — не прочитана) и причина, если прочитаны не все.

    known — уже прочитанное (ключ — отпечаток картинки): повторный разбор не платит за те же картинки."""
    known = known if known is not None else {}
    keys = [hashlib.sha256(p.data).hexdigest() for p in pictures]
    todo: dict[str, bytes] = {}
    error = ""
    for key, pic in zip(keys, pictures):
        if key in known or key in todo:
            continue
        if len(todo) >= MAX_SOURCE_PICTURES:
            error = f"картинок больше {MAX_SOURCE_PICTURES} — прочитаны первые"
            continue
        try:
            todo[key] = prepare_image(pic.data)
        except ValueError:  # формат, которого не знает Pillow (EMF, WMF), или битый файл
            error = error or "часть картинок в формате, который не читается"
    order = list(todo)
    batches = [order[i:i + SOURCE_BATCH] for i in range(0, len(order), SOURCE_BATCH)]

    def read(batch: list[str]) -> list[str | None]:
        texts = _ask_pictures([todo[k] for k in batch], llm)
        if texts is None and len(batch) > 1:  # ответ не сошёлся по числу: спрашиваем по одной
            texts = []
            for k in batch:
                one = _ask_pictures([todo[k]], llm)
                texts.append(one[0] if one else None)
        return texts or []

    def keep(batch: list[str], texts: list[str | None]) -> None:
        for k, t in zip(batch, texts):
            if t is not None:
                known[k] = t.strip()

    try:
        if batches:  # первая пачка — одна: модель без зрения или обрыв связи видны сразу, остальное не шлём
            keep(batches[0], read(batches[0]))
        if len(batches) > 1:  # остальные — одновременно: сервер читает картинку десятки секунд
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(SOURCE_WORKERS, len(batches) - 1)) as pool:
                for batch, texts in zip(batches[1:], pool.map(read, batches[1:])):
                    keep(batch, texts)
    except Exception as e:  # noqa: BLE001 — сеть, лимит, модель без зрения
        error = str(getattr(e, "message_ru", None) or e)
    return [known.get(k) for k in keys], error


_BLANK_LINES = re.compile(r"\n{3,}")


def put_pictures(text: str, pictures: list, texts: list[str | None]) -> str:
    """Ставит прочитанное на место меток. Страница целиком (скан, фото) — обычным текстом методички; картинка
    внутри текста — блоком «[Картинка в методичке: …]»; пустая (украшение) убирается вместе со своей строкой."""
    from forlabs.ingest.extract import CONTROL_CHARS, PICTURE_RE

    def one(m: re.Match) -> str:
        i = int(m.group(1)) - 1
        if not 0 <= i < len(texts) or texts[i] is None:
            return UNREAD_PICTURE
        got = CONTROL_CHARS.sub("", texts[i].replace("\r\n", "\n").replace("\r", "\n")).strip()
        if not got:
            return ""
        if pictures[i].page:
            return got
        return f"[Картинка в методичке:\n{got}\n]" if "\n" in got else f"[Картинка в методичке: {got}]"

    out = []
    for line in text.split("\n"):
        alone = PICTURE_RE.fullmatch(line.strip())
        if alone is None:
            out.append(PICTURE_RE.sub(one, line))
        elif got := one(alone):
            out.append(got)
    return _BLANK_LINES.sub("\n\n", "\n".join(out)).strip()


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
