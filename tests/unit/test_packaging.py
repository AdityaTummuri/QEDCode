"""Release hygiene: version, data files, changelog and the publish workflow stay consistent."""

from __future__ import annotations

import tomllib
from pathlib import Path

from qedcode import __version__

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_the_version_is_the_same_everywhere() -> None:
    assert PYPROJECT["project"]["version"] == __version__ == "0.1.0"
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{__version__}]" in changelog


def test_the_console_script_and_license_are_declared() -> None:
    project = PYPROJECT["project"]
    assert project["scripts"] == {"qed": "qedcode.cli:app"}
    assert project["license"] == {"file": "LICENSE"} and (ROOT / "LICENSE").exists()
    assert project["requires-python"] == ">=3.11"
    assert project["optional-dependencies"]["nvidia"]


def test_the_package_ships_its_data_files() -> None:
    assert PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/qedcode"]
    src = ROOT / "src" / "qedcode"
    for rel in (
        "runtime/catalog.yaml",
        "tui/app.tcss",
        "engine/prompts/system.txt",
        "engine/prompts/coder.txt",
        "engine/prompts/repairer.txt",
        "engine/prompts/protocol_cheatsheet.txt",
    ):
        assert (src / rel).is_file(), rel


def test_the_release_workflow_publishes_tags_with_trusted_publishing() -> None:
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'tags: ["v*"]' in text
    assert "id-token: write" in text and "pypa/gh-action-pypi-publish" in text
    assert "environment: pypi" in text
    assert "PYPI_API_TOKEN" not in text and "password:" not in text  # no stored secret
    assert "does not match version" in text  # the tag is checked against pyproject
    assert "pytest" in text  # tests run before publishing


def test_ci_checks_the_engine_purity_rule() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "Engine modules must not import UI modules" in text


def test_readme_documents_install_and_every_command() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "uv tool install qedcode" in readme and "pipx install qedcode" in readme
    for command in (
        "qed setup",
        "qed solve",
        "qed check",
        "qed verify",
        "qed apply",
        "qed undo",
        "qed connect",
        "qed quiet",
        "qed sandbox",
        "qed memory",
        "qed doctor",
        "qed uninstall",
        "qed mcp",
        "qed models",
    ):
        assert command in readme, command
    assert "Use with agents" in readme
