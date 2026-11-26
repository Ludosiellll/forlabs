"""Режим «задания отдельными файлами»: дефекты, найденные независимой проверкой кода, — как тесты.

Каждый тест сначала падал и воспроизводил дефект. Вёрстка самого отчёта идёт без LibreOffice (иначе каждый тест — десятки секунд): страницы документов
заданий даёт подмена Pipeline.page_renderer, как в tests/pipeline/test_task_files_stage.py."""

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).parents[1]
for sub in ("pipeline", "ui"):
    if str(TESTS / sub) not in sys.path:
        sys.path.insert(0, str(TESTS / sub))


@pytest.fixture(autouse=True)
def _no_office(monkeypatch, request):
    """Отчёт собирается без Word и LibreOffice — для этих проверок предпросмотр не нужен."""
    if request.node.get_closest_marker("real_office"):
        return
    from forlabs.render import libreoffice

    monkeypatch.setattr(libreoffice, "find_soffice", lambda configured=None: None)
