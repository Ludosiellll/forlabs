"""Особенности собранной программы (Nuitka).

Обёртка PyMuPDF pymupdf/mupdf.py (2,6 МБ сгенерированного кода) не компилируется в C — компилятору не хватает
памяти, — а лежит в папке программы файлом. Изнутри скомпилированного пакета pymupdf Nuitka её не находит, поэтому
до первого импорта pymupdf она загружается из папки как модуль верхнего уровня «mupdf» (вместе с бинарной частью
«_mupdf»): это запасной путь, которым её ищет сам pymupdf (`except Exception: import mupdf`).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path


def _load(name: str, path: Path) -> None:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"не загружается {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise


def preload_mupdf(base: Path) -> bool:
    """Загрузить _mupdf и mupdf из папки base (…/pymupdf). False — файлов нет (не собранная программа)."""
    base = Path(base)
    wrapper = base / "mupdf.py"
    binary = next((p for p in sorted(base.glob("_mupdf*")) if p.suffix in importlib.machinery.EXTENSION_SUFFIXES
                   or any(p.name.endswith(s) for s in importlib.machinery.EXTENSION_SUFFIXES)), None)
    if not wrapper.is_file() or binary is None:
        return False
    if "_mupdf" not in sys.modules:
        _load("_mupdf", binary)
    if "mupdf" not in sys.modules:
        _load("mupdf", wrapper)
    return True


def apply() -> None:
    """Вызывается точкой входа собранной программы до остального кода."""
    from forlabs.version import frozen

    if not frozen():
        return
    try:
        preload_mupdf(Path(sys.executable).resolve().parent / "pymupdf")
    except Exception:  # самопроверка покажет, что PDF не работает, с причиной
        pass
