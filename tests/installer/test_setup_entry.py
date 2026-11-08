"""run_setup.py (ForLabsSetup.exe) и tools/make_payload.py."""

import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _load(rel):
    spec = importlib.util.spec_from_file_location(Path(rel).stem, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_make_payload_packs_dist_folder(tmp_path):
    dist = tmp_path / "ForLabs.dist"
    (dist / "forlabs" / "rules").mkdir(parents=True)
    (dist / "ForLabs.exe").write_bytes(b"MZ")
    (dist / "forlabs" / "rules" / "a.md").write_text("т", "utf-8")
    out = tmp_path / "payload.zip"
    n = _load("tools/make_payload.py").make_payload(dist, out)
    assert n == 2
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["ForLabs.exe", "forlabs/rules/a.md"]


def test_make_payload_requires_program(tmp_path):
    (tmp_path / "d").mkdir()
    import pytest

    with pytest.raises(SystemExit):
        _load("tools/make_payload.py").make_payload(tmp_path / "d", tmp_path / "p.zip")


def test_setup_entry_uses_bundled_payload_and_silent(monkeypatch, tmp_path):
    from forlabs.installer import app

    seen = {}
    monkeypatch.setattr(app, "run", lambda payload, bundle, argv: seen.update(payload=payload, bundle=bundle, argv=argv) or 5)
    entry = _load("run_setup.py")
    assert entry.main(["--silent"]) == 5
    assert seen["payload"] == ROOT / "payload.zip" and seen["bundle"] == ROOT
    entry.main(["--payload", str(tmp_path / "x.zip")])
    assert seen["payload"] == tmp_path / "x.zip"
