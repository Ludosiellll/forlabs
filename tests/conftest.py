import pytest


@pytest.fixture(autouse=True)
def _work_in_temp_dir(tmp_path_factory, monkeypatch):
    """Тесты не пишут в папку репозитория, даже если путь по ошибке относительный (так в репозиторий однажды
    попали папки «C:» и «"/tmp/…», и Windows перестала его скачивать)."""
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("FORLABS_HOME", str(home))
    monkeypatch.delenv("FORLABS_API_KEY", raising=False)
    return home
