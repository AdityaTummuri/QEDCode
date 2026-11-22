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
    assert project["scripts"] == {"qed": "qedcode.cli:app", "qedcode": "qedcode.cli:app"}
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE", "NOTICE"]
    assert "Apache License" in (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Copyright 2026 Aditya Tummuri" in (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert not any(c.startswith("License ::") for c in project["classifiers"])  # PEP 639
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


def test_readme_is_a_short_first_screen_and_the_docs_cover_every_command() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert len(readme.encode("utf-8")) < 8 * 1024  # the 30-second pitch, not a manual
    for line in ("uvx qedcode try", "uvx qedcode check", "uv tool install qedcode"):
        assert line in readme
    install = (ROOT / "docs" / "install.md").read_text(encoding="utf-8")
    assert "uv tool install qedcode" in install and "pipx install qedcode" in install
    usage = (ROOT / "docs" / "usage.md").read_text(encoding="utf-8")
    for command in (
        "qed setup",
        "qed try",
        "qed solve",
        "qed check",
        "qed verify",
        "qed receipt",
        "qed apply",
        "qed undo",
        "qed tests",
        "qed hunt",
        "qed night",
        "qed connect",
        "qed quiet",
        "qed sandbox",
        "qed memory",
        "qed doctor",
        "qed uninstall",
        "qed mcp",
        "qed models",
        "qed benchy",
    ):
        assert command in usage, command
    assert "Use QEDCode from your coding agent" in (ROOT / "docs" / "agents.md").read_text(
        encoding="utf-8"
    )


def test_every_local_link_in_the_readme_and_docs_resolves() -> None:
    import re

    base = "https://github.com/AdityaTummuri/QEDCode/blob/main/"
    pages = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if target.startswith(base):
                path = ROOT / target[len(base) :].split("#")[0]
            elif "://" in target or target.startswith(("#", "mailto:")):
                continue
            else:
                path = page.parent / target.split("#")[0]
            assert path.exists(), f"{page.relative_to(ROOT)} links to a missing {target}"


def test_the_mcp_registry_entry_matches_the_package() -> None:
    import json

    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert server["version"] == __version__
    (package,) = server["packages"]
    assert package["identifier"] == PYPROJECT["project"]["name"] == "qedcode"
    assert package["version"] == __version__
    assert package["registryType"] == "pypi" and package["transport"] == {"type": "stdio"}
    assert package["packageArguments"] == [{"type": "positional", "value": "mcp"}]
    # the registry proves we own the PyPI package by finding this line in its README
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"<!-- mcp-name: {server['name']} -->" in readme
    assert len(server["description"]) <= 100


def test_actions_are_pinned_to_commits() -> None:
    import re

    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            match = re.search(r"uses:\s*([^\s#]+)", line)
            if match and not match.group(1).startswith("./"):
                ref = match.group(1).rsplit("@", 1)[-1]
                assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{workflow.name}: {line.strip()}"
