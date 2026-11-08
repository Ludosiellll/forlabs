from forlabs.core.rules import RuleBook, list_rule_files


def test_write_stage_includes_common_antiai_and_subject(tmp_home):
    text = RuleBook().load("write", "ssp")
    assert "Стандарт отчёта" in text
    assert "rules/subjects/ssp.md" in text and "rules/anti-ai/text.md" in text
    assert "rules/subjects/ks.md" not in text


def test_unknown_subject_uses_template(tmp_home):
    text = RuleBook().load("write", "oop")
    assert "rules/subjects/_template.md" not in text  # шаблон не подставляется как правила


def test_override_wins_and_reset(tmp_home):
    book = RuleBook()
    book.save_override("common/voice.md", "Пиши очень коротко.")
    assert "Пиши очень коротко." in book.load("write", "ssp")
    files = {f.path: f for f in book.files()}
    assert files["common/voice.md"].overridden is True
    book.reset_override("common/voice.md")
    assert "Пиши очень коротко." not in book.load("write", "ssp")


def test_custom_store_for_overrides(tmp_home):
    data = {}
    book = RuleBook(get_overrides=lambda: data, save=lambda p, t: data.__setitem__(p, t),
                    reset=lambda p: data.pop(p, None))
    book.save_override("anti-ai/text.md", "X")
    assert data == {"anti-ai/text.md": "X"}
    assert "X" in book.load("repair", "ssp")


def test_list_rule_files():
    paths = [f.path for f in list_rule_files()]
    assert "common/standard.md" in paths and "subjects/1c.md" in paths


import pytest


@pytest.mark.parametrize("bad", ["../pyproject.toml", "../../evil.md", "common/../../x.md", "/etc/passwd",
                                 "common/new.md"])
def test_only_listed_rule_files_can_be_saved_or_reset(tmp_home, bad):
    data = {}
    book = RuleBook(get_overrides=lambda: data, save=lambda p, t: data.__setitem__(p, t),
                    reset=lambda p: data.pop(p, None))
    with pytest.raises(KeyError):
        book.save_override(bad, "X")
    with pytest.raises(KeyError):
        book.reset_override(bad)
    assert data == {}
