"""Правила внутри exe: tools/embed_rules.py и RuleBook, который берёт встроенные."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from forlabs.core import rules

ROOT = Path(__file__).parents[2]


def _embed_tool():
    spec = importlib.util.spec_from_file_location("embed_rules", ROOT / "tools" / "embed_rules.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _fresh_builtin(monkeypatch):
    monkeypatch.setattr(rules, "_BUILTIN", None)
    yield
    rules._BUILTIN = None


def test_rules_ship_inside_package():
    paths = {f.path for f in rules.list_rule_files()}
    assert {"common/standard.md", "common/voice.md", "anti-ai/text.md", "subjects/ssp.md"} <= paths
    assert rules.RULES_DIR == ROOT / "forlabs" / "rules"


def test_embedded_module_roundtrip_and_not_plain_text(tmp_path):
    tool = _embed_tool()
    out = tmp_path / "_embedded_rules.py"
    tool.write_module(rules.RULES_DIR, out)
    src = out.read_text("utf-8")
    standard = (rules.RULES_DIR / "common" / "standard.md").read_text("utf-8")
    phrase = next(line for line in standard.splitlines() if len(line) > 30)
    assert phrase not in src and phrase.encode("utf-8") not in out.read_bytes()
    ns: dict = {}
    exec(compile(src, str(out), "exec"), ns)
    loaded = ns["load"]()
    expected = {p.relative_to(rules.RULES_DIR).as_posix(): p.read_text("utf-8") for p in rules.RULES_DIR.rglob("*.md")}
    assert loaded == expected


def test_rulebook_prefers_embedded(monkeypatch):
    fake = types.ModuleType("forlabs._embedded_rules")
    fake.load = lambda: {"common/standard.md": "ВСТРОЕННОЕ", "subjects/ssp.md": "предмет"}
    monkeypatch.setitem(sys.modules, "forlabs._embedded_rules", fake)
    book = rules.RuleBook(get_overrides=dict)
    assert book.text("common/standard.md") == "ВСТРОЕННОЕ"
    assert book.text("common/voice.md") is None
    assert [f.path for f in rules.list_rule_files()] == ["common/standard.md", "subjects/ssp.md"]
    assert "<!-- rules/subjects/ssp.md -->\nпредмет" in book.load("write", "ssp")


def test_overrides_still_win_over_embedded(monkeypatch):
    fake = types.ModuleType("forlabs._embedded_rules")
    fake.load = lambda: {"common/standard.md": "ВСТРОЕННОЕ"}
    monkeypatch.setitem(sys.modules, "forlabs._embedded_rules", fake)
    book = rules.RuleBook(get_overrides=lambda: {"common/standard.md": "СВОЁ"})
    assert book.text("common/standard.md") == "СВОЁ"
