"""Print one version's section of CHANGELOG.md (the GitHub Release notes for that tag).

uv run python scripts/changelog_section.py 0.2.0 > notes.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def section(changelog: str, version: str) -> str:
    """The text under `## [version]` up to the next `## [`; ValueError when it is missing."""
    version = version.removeprefix("v")
    match = re.search(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", changelog, re.S | re.M
    )
    if not match or not match.group(1).strip():
        raise ValueError(
            f"CHANGELOG.md has no notes for {version}. Move the Unreleased notes under "
            f"'## [{version}] - <date>' before tagging."
        )
    return match.group(1).strip() + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: changelog_section.py <version>", file=sys.stderr)
        return 2
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    try:
        sys.stdout.write(section(text, argv[0]))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
