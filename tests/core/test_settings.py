import json

from forlabs.core import paths
from forlabs.core.settings import Settings, get_api_key, load_settings, save_settings


def test_data_home_respects_env(tmp_home):
    assert paths.data_home() == tmp_home


def test_settings_roundtrip(tmp_home):
    s = Settings(model="deepseek-v4-flash", word_path=r"C:\Word\WINWORD.EXE", temperature=0.3)
    save_settings(s)
    loaded = load_settings()
    assert loaded == s
    assert (tmp_home / "settings.json").exists()


def test_defaults_point_to_aikeys(tmp_home):
    s = load_settings()
    assert s.base_url == "https://api.aikeysforyou.com/v1"
    assert s.model == "deepseek-v4.1-flash-1017"  # владелец: по умолчанию всегда 1017


def test_api_key_env_fallback(tmp_home, monkeypatch):
    monkeypatch.setattr("forlabs.core.settings._keyring_get", lambda: None)
    monkeypatch.setenv("FORLABS_API_KEY", "sk-test-123")
    assert get_api_key() == "sk-test-123"
    save_settings(load_settings())
    assert "sk-test-123" not in (tmp_home / "settings.json").read_text(encoding="utf-8")
    assert "api_key" not in json.loads((tmp_home / "settings.json").read_text(encoding="utf-8"))


def test_default_model_is_v41_flash_1017():
    from forlabs.core.settings import Settings

    assert Settings().model == "deepseek-v4.1-flash-1017"
    # старое значение по умолчанию из прошлых версий поднимается до 1017
    assert Settings.model_validate({"model": "deepseek-v4.1-flash"}).model == "deepseek-v4.1-flash-1017"
    assert Settings.model_validate({"model": "deepseek-v4-flash"}).model == "deepseek-v4-flash"  # явный выбор


def test_model_choices_can_all_be_chosen():
    from forlabs.core.settings import MODEL_CHOICES, Settings

    for m in MODEL_CHOICES:
        assert Settings.model_validate({"model": m}).model == m
