"""Сайты издателей и запуск программ — подделки (toolfakes.py): тесты не ходят в интернет и не запускают .exe."""

import pytest
from toolfakes import Runs, Web

from forlabs.runner.tools import ToolManager


@pytest.fixture
def web():
    return Web()


@pytest.fixture
def make_mgr(tmp_path):
    def make(web: Web, **kw) -> ToolManager:
        opened = kw.pop("opened", [])
        m = ToolManager(root=tmp_path / "tools", http=web.client, downloads=tmp_path / "Загрузки",
                        open_file=opened.append, detect=kw.pop("detect", lambda tools_dir: {}),
                        run=kw.pop("run", None) or Runs())
        m.pauses = (0, 0, 0)
        m.free_space = lambda p: 10**12
        m.opened = opened
        return m

    return make
