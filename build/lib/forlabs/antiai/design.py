"""Детектор ИИ-штампов в дизайне (HTML/CSS страниц, прототипов). Креатив допустим, штампы — нет."""

from __future__ import annotations

import re

from forlabs.core.models import Finding

_CHECKS: list[tuple[str, re.Pattern, str, str]] = [
    ("design.ai_gradient", re.compile(r"#667eea|#764ba2|linear-gradient\([^)]*(?:#6[0-9a-f]{2}[ef][0-9a-f]{2}|purple|indigo|violet)",
                                      re.IGNORECASE), "Типовой ИИ-градиент", "палитра от темы работы"),
    ("design.glass", re.compile(r"backdrop-filter", re.IGNORECASE), "Эффект «стекла»", "обычный фон"),
    ("design.neon", re.compile(r"box-shadow\s*:[^;}]*\b0\s+0\s+(?:[2-9]\d|\d{3})px\s+(?:#(?!000|111|222|333)[0-9a-f]{3,6}|rgba?\((?!0,\s*0,\s*0))",
                               re.IGNORECASE), "Неоновое свечение", "мягкая тень или без неё"),
    ("design.default_font", re.compile(r"font-family\s*:\s*['\"]?(?:Inter|Poppins)\b", re.IGNORECASE),
     "Шрифт по умолчанию ИИ-макетов", "шрифт, подходящий теме"),
    ("design.emoji_icons", re.compile("[\U0001F300-\U0001FAFF✅⭐✨]"), "Эмодзи вместо иконок",
     "SVG-иконки или изображения"),
    ("design.slogan", re.compile(r"Откройте\s+для\s+себя|Добро\s+пожаловать\s+в\s+будущее|Погрузитесь\s+в|"
                                 r"Ваш\s+(?:путь|ключ)\s+к|Новый\s+уровень", re.IGNORECASE),
     "Рекламный лозунг", "конкретный заголовок по теме"),
]


def scan_design(source: str, location: str) -> list[Finding]:
    out = []
    for rule, rx, msg, sug in _CHECKS:
        m = rx.search(source)
        if m:
            out.append(Finding(stage="antiai", rule_id=rule, severity="error", location=location,
                               text=m.group(0), message=msg, suggestion=sug))
    return out
