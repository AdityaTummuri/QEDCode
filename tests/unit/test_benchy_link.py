"""`qed benchy`: optional, asks before installing, runs only fixed commands, never by itself."""

from __future__ import annotations

import sys

from typer.testing import CliRunner

from qedcode import benchy_link
from qedcode.cli import app
from qedcode.verify.run import RunResult

runner = CliRunner()


def _not_installed(monkeypatch) -> None:
    monkeypatch.setattr(benchy_link, "installed_version", lambda: None)


def test_status_when_not_installed(monkeypatch) -> None:
    _not_installed(monkeypatch)
    result = runner.invoke(app, ["benchy"])
    assert result.exit_code == 0
    assert "not installed" in result.output and "qed benchy install" in result.output


def test_install_asks_first_and_runs_nothing_on_no(monkeypatch) -> None:
    _not_installed(monkeypatch)
    ran: list = []
    monkeypatch.setattr(benchy_link, "run", lambda *a, **k: ran.append(a) or RunResult(args=[]))
    asked: list[str] = []
    result = benchy_link.install(lambda prompt: asked.append(prompt) or False)
    assert not result.ok and "Nothing was installed" in result.message
    assert asked and "only when you type" in asked[0]
    assert ran == []


def test_install_uses_uv_with_qedcodes_python(monkeypatch) -> None:
    state = {"version": None}
    monkeypatch.setattr(benchy_link, "installed_version", lambda: state["version"])
    monkeypatch.setattr(benchy_link.shutil, "which", lambda name: "/bin/uv")
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        state["version"] = "0.1.0"
        return RunResult(args=list(cmd), exit_code=0)

    monkeypatch.setattr(benchy_link, "run", fake_run)
    result = benchy_link.install(lambda _p: True, source="/some/checkout")
    assert result.ok and "0.1.0" in result.message
    assert seen == [["/bin/uv", "pip", "install", "--python", sys.executable, "/some/checkout"]]


def test_install_failure_is_explained(monkeypatch) -> None:
    _not_installed(monkeypatch)
    monkeypatch.setattr(benchy_link.shutil, "which", lambda name: "/bin/uv")
    monkeypatch.setattr(
        benchy_link, "run", lambda cmd, **k: RunResult(args=cmd, exit_code=1, stderr="no network")
    )
    result = benchy_link.install(lambda _p: True)
    assert not result.ok and "no network" in result.message and "by hand" in result.message


def test_no_uv_and_no_pip(monkeypatch) -> None:
    _not_installed(monkeypatch)
    monkeypatch.setattr(benchy_link.shutil, "which", lambda name: None)
    monkeypatch.setattr(benchy_link.importlib.util, "find_spec", lambda name: None)
    result = benchy_link.install(lambda _p: True)
    assert not result.ok and "neither uv nor pip" in result.message


def test_uninstall_when_not_installed_is_a_no_op(monkeypatch) -> None:
    _not_installed(monkeypatch)
    assert benchy_link.uninstall(lambda _p: True).ok


def test_launch_needs_benchy(monkeypatch) -> None:
    _not_installed(monkeypatch)
    assert benchy_link.launch(["run"]) == 1


def test_cli_passes_arguments_through(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(benchy_link, "launch", lambda args: calls.append(list(args)) or 0)
    result = runner.invoke(app, ["benchy", "run", "--reps", "3", "--help"])
    assert result.exit_code == 0
    assert calls == [["run", "--reps", "3", "--help"]]


def test_cli_exit_code_follows_benchy(monkeypatch) -> None:
    monkeypatch.setattr(benchy_link, "launch", lambda args: 2)
    assert runner.invoke(app, ["benchy", "report"]).exit_code == 2
