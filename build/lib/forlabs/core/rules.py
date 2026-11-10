"""Правила для модели: поставляемые .md-файлы + правки пользователя поверх них."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from forlabs.core import paths
from forlabs.core.fs import atomic_write_text

RULES_DIR = Path(__file__).resolve().parents[1] / "rules"
Stage = Literal["write", "repair", "code", "design"]

STAGE_FILES: dict[str, list[str]] = {
    "write": ["common/standard.md", "common/voice.md", "anti-ai/text.md", "anti-ai/code.md", "anti-ai/design.md"],
    "repair": ["common/standard.md", "common/voice.md", "anti-ai/text.md"],
    "code": ["anti-ai/code.md"],
    "design": ["anti-ai/design.md"],
}


@dataclass
class RuleFile:
    path: str
    text: str
    overridden: bool = False

    @property
    def title(self) -> str:
        return rule_title(self.path)


_TITLES = {"common/standard.md": "Стандарт отчёта", "common/voice.md": "Голос студента",
           "anti-ai/text.md": "Анти-ИИ: текст", "anti-ai/code.md": "Анти-ИИ: код",
           "anti-ai/design.md": "Анти-ИИ: дизайн", "subjects/_template.md": "Шаблон для нового предмета"}


def rule_title(path: str) -> str:
    if path in _TITLES:
        return _TITLES[path]
    if path.startswith("subjects/"):
        from forlabs.core.subjects import all_subjects

        sid = path.split("/", 1)[1].removesuffix(".md")
        subj = all_subjects().get(sid)
        return f"Предмет: {subj.name if subj else sid}"
    return path


def _order(path: str) -> tuple:
    group = {"common": 0, "anti-ai": 1, "subjects": 2}.get(path.split("/")[0], 3)
    return (group, path.endswith("_template.md"), rule_title(path))


_BUILTIN: dict[str, str] | None = None
_SOURCE = "none"


def builtin_rules() -> dict[str, str]:
    """Поставляемые правила: в собранном exe — встроенные в машинный код (forlabs/_embedded_rules.py создаёт
    tools/embed_rules.py при сборке, папки rules рядом с программой нет), иначе — файлы пакета."""
    global _BUILTIN, _SOURCE
    if _BUILTIN is None:
        try:
            from forlabs import _embedded_rules  # type: ignore[attr-defined]

            _BUILTIN, _SOURCE = _embedded_rules.load(), "embedded"
        except ImportError:
            _BUILTIN = {p.relative_to(RULES_DIR).as_posix(): p.read_text(encoding="utf-8")
                        for p in RULES_DIR.rglob("*.md")} if RULES_DIR.exists() else {}
            _SOURCE = "files" if _BUILTIN else "none"
    return _BUILTIN


def rules_source() -> str:
    """embedded — встроены в программу, files — папка пакета, none — правил нет."""
    builtin_rules()
    return _SOURCE


def list_rule_files() -> list[RuleFile]:
    files = [RuleFile(path, text) for path, text in builtin_rules().items()]
    return sorted(files, key=lambda f: _order(f.path))


def _default_overrides() -> dict[str, str]:
    d = paths.data_home() / "rules"
    if not d.exists():
        return {}
    return {p.relative_to(d).as_posix(): p.read_text(encoding="utf-8") for p in d.rglob("*.md")}


def _default_save(path: str, text: str) -> None:
    atomic_write_text(paths.data_home() / "rules" / path, text)


def _default_reset(path: str) -> None:
    f = paths.data_home() / "rules" / path
    if f.exists():
        f.unlink()


class RuleBook:
    def __init__(self, get_overrides: Callable[[], dict[str, str]] | None = None,
                 save: Callable[[str, str], None] | None = None, reset: Callable[[str], None] | None = None):
        self._get = get_overrides or _default_overrides
        self._save = save or _default_save
        self._reset = reset or _default_reset

    def text(self, path: str, overrides: dict[str, str] | None = None) -> str | None:
        overrides = self._get() if overrides is None else overrides
        if path in overrides:
            return overrides[path]
        return builtin_rules().get(path)

    def load(self, stage: Stage, subject_id: str) -> str:
        overrides = self._get()
        files = list(STAGE_FILES[stage])
        if stage in ("write", "code", "design"):
            files.append(f"subjects/{subject_id}.md")
        parts = []
        for path in files:
            t = self.text(path, overrides)
            if t:
                parts.append(f"<!-- rules/{path} -->\n{t.strip()}")
        return "\n\n".join(parts)

    def files(self) -> list[RuleFile]:
        overrides = self._get()
        out = []
        for rf in list_rule_files():
            if rf.path in overrides:
                out.append(RuleFile(rf.path, overrides[rf.path], True))
            else:
                out.append(rf)
        return out

    @staticmethod
    def _check(path: str) -> None:
        # Только файлы из списка правил: путь приходит из формы и не должен выходить за папку правил.
        if path not in {rf.path for rf in list_rule_files()}:
            raise KeyError(f"Нет файла правил {path}")

    def save_override(self, path: str, text: str) -> None:
        self._check(path)
        self._save(path, text)

    def reset_override(self, path: str) -> None:
        self._check(path)
        self._reset(path)
