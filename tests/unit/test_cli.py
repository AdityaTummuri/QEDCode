from typer.testing import CliRunner

from qedcode import __version__
from qedcode.cli import app

runner = CliRunner()


def test_version_flag_prints_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__ == "0.0.1"


def test_no_arguments_says_not_ready() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "QEDCode is not ready yet" in result.stdout
