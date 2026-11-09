"""Версия ForLabs (установщик, сборка и /health берут её отсюда)."""

__version__ = "1.0.7"


def frozen() -> bool:
    """Собранная программа (Nuitka кладёт __compiled__ в каждый скомпилированный модуль)."""
    return "__compiled__" in globals()
