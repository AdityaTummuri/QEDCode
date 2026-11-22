"""Write the README's stats block (lines of source, tests, platforms) between its markers.

The release workflow runs this before building, so the numbers on GitHub and PyPI are measured,
never typed by hand:

    uv run python scripts/readme_stats.py [--readme README.md] [--version 0.2.0] [--check]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START, END = "<!-- stats:start -->", "<!-- stats:end -->"


def source_lines(root: Path = ROOT) -> int:
    """Non-blank lines in the package's Python source."""
    total = 0
    for path in (root / "src" / "qedcode").rglob("*.py"):
        if "tour_data" in path.parts:  # demo projects, not QEDCode's own code
            continue
        text = path.read_text(encoding="utf-8")
        total += sum(1 for line in text.splitlines() if line.strip())
    return total


def test_count(root: Path = ROOT) -> int:
    """How many tests CI runs (`pytest -m "not gpu" --collect-only`)."""
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "not gpu",
            "-p",
            "no:cacheprovider",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"(\d+)(?:/\d+)? tests? (?:collected|selected)", done.stdout)
    if not match:
        raise RuntimeError(
            f"Could not count the tests:\n{done.stdout[-2000:]}\n{done.stderr[-2000:]}"
        )
    return int(match.group(1))


def platforms(root: Path = ROOT) -> tuple[list[str], list[str]]:
    """The operating systems and Python versions in the CI matrix."""
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    names = {"ubuntu": "Linux", "macos": "macOS", "windows": "Windows"}
    os_line = re.search(r"os:\s*\[([^\]]+)\]", ci)
    py_line = re.search(r"python:\s*\[([^\]]+)\]", ci)
    found = [s.strip().split("-")[0] for s in os_line.group(1).split(",")] if os_line else []
    systems = [names.get(s, s) for s in found]
    pythons = [s.strip().strip("\"'") for s in py_line.group(1).split(",")] if py_line else []
    return sorted(systems, key=["Linux", "macOS", "Windows"].index), pythons


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def render(lines: int, tests: int, systems: list[str], pythons: list[str], version: str) -> str:
    when = time.strftime("%Y-%m-%d", time.gmtime())
    label = f"v{version}, " if version else ""
    return (
        f"{START}\n"
        f"**{lines:,} lines of source · {tests:,} automated tests · CI on {_and(systems)} "
        f"with Python {' and '.join(pythons)}** "
        f"<sub>(measured by the release workflow: {label}{when})</sub>\n"
        f"{END}"
    )


def replace_block(text: str, block: str) -> str:
    """Put `block` between the markers; a README without them is an error, not a silent no-op."""
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        raise ValueError(f"The README has no {START} ... {END} markers.")
    return pattern.sub(lambda _m: block, text, count=1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--readme", default=str(ROOT / "README.md"))
    parser.add_argument("--version", default="")
    parser.add_argument("--tests", type=int, help="use this test count instead of collecting")
    args = parser.parse_args(argv)
    systems, pythons = platforms()
    block = render(source_lines(), args.tests or test_count(), systems, pythons, args.version)
    path = Path(args.readme)
    path.write_text(replace_block(path.read_text(encoding="utf-8"), block), encoding="utf-8")
    print(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
