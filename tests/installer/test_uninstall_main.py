"""ForLabs.exe --uninstall: вопросы, работающий ForLabs, тихий режим."""

from pathlib import Path

from forlabs.installer import core, uninstall

from .conftest import FakeShell


class Dialogs:
    def __init__(self, answers):
        self.answers, self.asked, self.told = list(answers), [], []

    def ask(self, text, default_no=False):
        self.asked.append(text)
        return self.answers.pop(0)

    def tell(self, text, warning=False):
        self.told.append(text)


def _installed(tmp_path, payload, shell):
    target = tmp_path / "ForLabs"
    core.install(payload, target, shell=shell)
    return target


def _run(argv, target, shell, dialogs, data, running=False, keys=None):
    return uninstall.run(argv, target=target, shell=shell, dialogs=dialogs, data=data,
                         is_running=lambda: running, exe=None, forget_keys=(keys if keys is not None else []).append)


def test_declined_changes_nothing(tmp_path, payload, shell):
    target = _installed(tmp_path, payload, shell)
    d = Dialogs([False])
    assert _run([], target, shell, d, tmp_path / "data") == 0
    assert target.exists() and shell.entry is not None


def test_running_forlabs_blocks_uninstall(tmp_path, payload, shell):
    target = _installed(tmp_path, payload, shell)
    d = Dialogs([True])
    assert _run([], target, shell, d, tmp_path / "data", running=True) == 2
    assert "закрой" in d.told[0] and target.exists()


def test_keeps_data_by_default_answer(tmp_path, payload, shell):
    target, data = _installed(tmp_path, payload, shell), tmp_path / "data"
    data.mkdir()
    keys = []
    d = Dialogs([True, False])
    assert _run([], target, shell, d, data, keys=keys) == 0
    assert not target.exists() and data.exists() and keys == []
    assert "облачном аккаунте" in d.asked[1] and "удалён" in d.told[-1]


def test_removes_data_and_saved_logins_when_asked(tmp_path, payload, shell):
    target, data = _installed(tmp_path, payload, shell), tmp_path / "data"
    data.mkdir()
    keys = []
    assert _run([], target, shell, Dialogs([True, True]), data, keys=keys) == 0
    assert not data.exists() and keys == [True]


def test_silent_asks_nothing_and_keeps_data(tmp_path, payload, shell):
    target, data = _installed(tmp_path, payload, shell), tmp_path / "data"
    data.mkdir()
    d = Dialogs([])
    assert _run(["--uninstall", "--silent"], target, shell, d, data) == 0
    assert not target.exists() and data.exists() and d.asked == [] and d.told == []


def test_silent_remove_data_flag(tmp_path, payload, shell):
    target, data = _installed(tmp_path, payload, shell), tmp_path / "data"
    data.mkdir()
    assert _run(["--silent", "--remove-data"], target, shell, Dialogs([]), data) == 0
    assert not data.exists()


def test_silent_running_returns_error_code(tmp_path, payload, shell):
    target = _installed(tmp_path, payload, shell)
    assert _run(["--silent"], target, shell, Dialogs([]), tmp_path / "d", running=True) == 2


def test_foreign_folder_reports_error(tmp_path, shell):
    docs = tmp_path / "Документы"
    docs.mkdir()
    (docs / "a.txt").write_text("x")
    d = Dialogs([True, False])
    assert _run([], docs, shell, d, tmp_path / "d") == 1
    assert "нет ForLabs" in d.told[-1] and (docs / "a.txt").exists()


def test_target_flag_overrides(tmp_path):
    assert uninstall.target_from(["--target", str(tmp_path / "X")], exe=None) == tmp_path / "X"
    assert uninstall.target_from([], exe=Path("/p/ForLabs/ForLabs.exe")) == Path("/p/ForLabs")


def test_fake_shell_is_protocol_compatible():
    shell: core.Shell = FakeShell(Path("a"), Path("b"))
    assert shell.read_uninstall_entry() is None
