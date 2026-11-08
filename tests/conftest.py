import pytest


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("FORLABS_HOME", str(home))
    monkeypatch.delenv("FORLABS_API_KEY", raising=False)
    return home
