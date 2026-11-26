"""Режим «задания отдельными файлами»: документ Word на каждое задание лежит в папке сдачи, а в отчёте вместо
текста задания — снимки страниц этого документа. Так теперь требуют преподаватели: задание сдаётся своим файлом.

Здесь — то, что не зависит от конвейера: картинки страниц для отчёта и производный отчёт «подводка + снимки».
Исходник документа — блоки задания в отчёте (TaskSection.blocks): они хранятся как всегда и не меняются."""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageChops

from forlabs.core.models import Figure, ReportDoc, TaskSection

ANSWERS = "answers"  # ключ документа с ответами на контрольные вопросы
ANSWERS_TITLE = "Ответы на контрольные вопросы"
PAD = 24  # точек белого поля вокруг содержимого страницы на снимке
GAP = 28  # между двумя страницами на одном снимке
ONE_PAGE_CM, TWO_PAGES_CM = 11.0, 16.5  # ширина снимка в отчёте: одна страница и две рядом
_PRESENTATION = re.compile(r"презентаци", re.IGNORECASE)
_WORD = re.compile(r"(?<![A-Za-zА-Яа-яЁё])(?:word(?![A-Za-z])|ворд[а-яё]*)", re.IGNORECASE)  # не Password и не WordPress
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def is_presentation(task_text: str) -> bool:
    """Задание просит презентацию: её файл (PowerPoint) ForLabs не делает — слайды остаются студенту."""
    return bool(_PRESENTATION.search(task_text or ""))


def ready(section: TaskSection) -> bool:
    """Из задания можно собрать документ: в нём есть содержимое и не осталось снимков, которые делает студент."""
    has = any(getattr(b, "text", "").strip() or getattr(b, "items", None) or getattr(b, "code", "").strip()
              or getattr(b, "rows", None) or isinstance(b, Figure) for b in section.blocks)
    return has and all(b.image_path for b in section.blocks if isinstance(b, Figure))


def _document_shot(block) -> bool:
    """Место под снимок самого документа задания: ждёт студента (или агента в Word) и говорит про Word."""
    if not isinstance(block, Figure) or block.image_path:
        return False
    if block.app_task is not None:
        return block.app_task.app == "word"
    return bool(_WORD.search(f"{block.topic} {block.slot or ''}"))


def drop_document_shots(doc: ReportDoc) -> list[int]:
    """Убрать из заданий места под «снимок документа в Word». В обычном режиме автор отчёта оставляет их студенту;
    в режиме файлов этот документ и есть файл задания, а с таким местом задание ждало бы снимка и осталось без
    файла. Вставленные снимки и снимки других программ не трогаются. → номера заданий, где что-то убрано."""
    changed = []
    for sec in doc.sections:
        keep = [b for b in sec.blocks if not _document_shot(b)]
        if len(keep) != len(sec.blocks):
            sec.blocks = keep
            changed.append(sec.n)
    return changed


def title_of(section: TaskSection) -> str:
    return section.doc_title.strip().rstrip(".") or f"Результат выполнения задания {section.n}"


def file_name(section: TaskSection) -> str:
    """Имя файла задания в папке сдачи: «Задание 2 — Вопросы интервью.docx» (без знаков, запрещённых в Windows)."""
    title = " ".join(_UNSAFE.sub(" ", section.doc_title).split()).strip(" .")
    name = f"Задание {section.n}" + (f" — {title}" if title else "")
    return name[:85].rstrip(" .—") + ".docx"


def crop_page(png: Path) -> Image.Image:
    """Страница без белых полей листа (с небольшим отступом): текст на снимке в отчёте крупнее."""
    im = Image.open(png).convert("RGB")
    box = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).getbbox()
    if box is None:
        return im
    return im.crop((max(0, box[0] - PAD), max(0, box[1] - PAD), min(im.width, box[2] + PAD), min(im.height, box[3] + PAD)))


def sheets(pages: list[Path], out_dir: Path, prefix: str) -> list[tuple[Path, str]]:
    """Снимки для отчёта из страниц документа: одна страница — одним снимком, больше — по две рядом.
    → [(файл, подпись «страницы 1–2» | «страница 3» | «» для документа в одну страницу)]."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{prefix}_*.png"):
        old.unlink(missing_ok=True)
    out: list[tuple[Path, str]] = []
    for i in range(0, len(pages), 2):
        pair = [crop_page(p) for p in pages[i:i + 2]]
        if len(pair) == 2:
            sheet = Image.new("RGB", (pair[0].width + GAP + pair[1].width, max(x.height for x in pair)), (255, 255, 255))
            sheet.paste(pair[0], (0, 0))
            sheet.paste(pair[1], (pair[0].width + GAP, 0))
            label = f"страницы {i + 1}–{i + 2}"
        else:
            sheet, label = pair[0], f"страница {i + 1}"
        path = out_dir / f"{prefix}_{i // 2 + 1}.png"
        sheet.save(path, dpi=(144, 144))
        out.append((path, label))
    if len(out) == 1:  # документ целиком на одном снимке — уточнять страницы незачем
        out = [(out[0][0], "")]
    return out


def _figures(title: str, shots: list[tuple[Path, str]]) -> list[Figure]:
    def width(path: Path) -> float:
        try:
            with Image.open(path) as im:
                return TWO_PAGES_CM if im.width > im.height else ONE_PAGE_CM
        except OSError:
            return ONE_PAGE_CM

    return [Figure(topic=f"{title} ({label})" if label else title, image_path=str(path), width_cm=width(Path(path)))
            for path, label in shots]


def as_screens(doc: ReportDoc, shots: dict) -> ReportDoc:
    """Отчёт для вёрстки: у заданий, чей документ снят, вместо содержимого — подводки и снимки его страниц; ответы
    на вопросы — тоже снимком. shots: {номер задания | ANSWERS: [(картинка, подпись)]}. Исходный отчёт не меняется."""
    out = doc.model_copy(deep=True)
    for sec in out.sections:
        if shots.get(sec.n):
            sec.blocks = _figures(title_of(sec), shots[sec.n])
    if shots.get(ANSWERS) and out.questions:
        out.answer_figures = _figures(ANSWERS_TITLE, shots[ANSWERS])
        out.questions = []
    return out
