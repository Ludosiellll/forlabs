"""Правила для модели: поставляемые .md-файлы + правки пользователя поверх них."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from forlabs.core import paths

RULES_DIR = Path(__file__).resolve().parents[2] / "rules"
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


def list_rule_files() -> list[RuleFile]:
    return [RuleFile(p.relative_to(RULES_DIR).as_posix(), p.read_text(encoding="utf-8"))
            for p in sorted(RULES_DIR.rglob("*.md"))]


def _default_overrides() -> dict[str, str]:
    d = paths.data_home() / "rules"
    if not d.exists():
        return {}
    return {p.relative_to(d).as_posix(): p.read_text(encoding="utf-8") for p in d.rglob("*.md")}


def _default_save(path: str, text: str) -> None:
    f = paths.data_home() / "rules" / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


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
        f = RULES_DIR / path
        return f.read_text(encoding="utf-8") if f.exists() else None

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

    def save_override(self, path: str, text: str) -> None:
        if not (RULES_DIR / path).exists():
            raise KeyError(f"Нет файла правил {path}")
        self._save(path, text)

    def reset_override(self, path: str) -> None:
        self._reset(path)
