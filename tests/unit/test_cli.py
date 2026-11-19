from pathlib import Path

from typer.testing import CliRunner

from qedcode import __version__
from qedcode.cli import app

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__ == "0.0.1"


def test_no_arguments_opens_the_tui_in_the_current_folder(monkeypatch, tmp_path) -> None:
    opened: list[Path] = []
    monkeypatch.setattr("qedcode.tui.app.run_tui", lambda root: opened.append(Path(root)))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert opened == [Path.cwd()]
