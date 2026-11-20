"""Optional Benchy integration: install, remove and launch QEDCode Benchy (`qed benchy ...`).

Benchy (https://github.com/AdityaTummuri/QEDCode-benchy) measures QEDCode against the same model
without QEDCode. It is optional: QEDCode never needs it, never installs it without a yes, and never
runs it unless the user types a `qed benchy` command. Nothing here sends any data anywhere.

Benchy is installed into the Python environment QEDCode runs in (so it can call the engine), with
`uv pip` when uv is available, else with pip. Only these fixed commands run, with list arguments
and no shell.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from qedcode.verify.run import run

DIST_NAME = "qedcode-benchy"
DEFAULT_SOURCE = f"{DIST_NAME} @ git+https://github.com/AdityaTummuri/QEDCode-benchy"
HOMEPAGE = "https://github.com/AdityaTummuri/QEDCode-benchy"
INSTALL_TIMEOUT_S = 900.0
Confirm = Callable[[str], bool]


@dataclass
class LinkResult:
    ok: bool
    message: str


def installed_version() -> str | None:
    """Benchy's version in this environment, or None when it is not installed."""
    try:
        return importlib.metadata.version(DIST_NAME)
    except importlib.metadata.PackageNotFoundError:
        return None


def _installer(action: str, target: str) -> list[str] | None:
    """The fixed install/uninstall command for this environment (None: no uv and no pip)."""
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", action, "--python", sys.executable, target]
    if importlib.util.find_spec("pip") is not None:
        cmd = [sys.executable, "-m", "pip", action, target]
        return cmd + (["-y"] if action == "uninstall" else [])
    return None


def install(confirm: Confirm, source: str = DEFAULT_SOURCE) -> LinkResult:
    """Install Benchy (asks first: it downloads Benchy and pytest)."""
    if installed_version() is not None:
        return LinkResult(True, f"Benchy {installed_version()} is already installed.")
    cmd = _installer("install", source)
    if cmd is None:
        return LinkResult(
            False,
            "Cannot install Benchy: neither uv nor pip is available for QEDCode's Python. "
            "Install uv (https://docs.astral.sh/uv/), then run `qed benchy install` again.",
        )
    prompt = (
        f"Install the optional Benchy benchmark from {source.split('@', 1)[-1].strip()}?\n"
        "It downloads Benchy and pytest into QEDCode's environment. Benchy runs only when you "
        "type `qed benchy ...` and keeps its reports on this computer."
    )
    if not confirm(prompt):
        return LinkResult(False, "Nothing was installed.")
    result = run(cmd, timeout=INSTALL_TIMEOUT_S, scrub=False)
    if not result.ok or installed_version() is None:
        detail = (result.error or result.stderr or result.stdout).strip().splitlines()[-1:] or [""]
        return LinkResult(
            False,
            f"Installing Benchy failed ({detail[0][:200]}). Check your network connection and "
            "run `qed benchy install` again, or install it by hand: " + " ".join(cmd),
        )
    return LinkResult(
        True,
        f"Installed Benchy {installed_version()}. Try `qed benchy selftest`, then `qed benchy run`."
        " If `uv tool upgrade qedcode` later removes it, run `qed benchy install` again.",
    )


def uninstall(confirm: Confirm) -> LinkResult:
    """Remove Benchy. Reports already written stay in ~/.qedcode/benchy/reports."""
    if installed_version() is None:
        return LinkResult(True, "Benchy is not installed.")
    cmd = _installer("uninstall", DIST_NAME)
    if cmd is None:
        return LinkResult(False, "Cannot remove Benchy: neither uv nor pip is available.")
    if not confirm("Remove Benchy? Your saved reports are kept."):
        return LinkResult(False, "Nothing was removed.")
    result = run(cmd, timeout=INSTALL_TIMEOUT_S, scrub=False)
    if not result.ok:
        return LinkResult(False, f"Removing Benchy failed. Run it by hand: {' '.join(cmd)}")
    return LinkResult(True, "Benchy was removed. QEDCode works exactly as before.")


def status_lines() -> list[str]:
    version = installed_version()
    if version is None:
        return [
            "Benchy is not installed (it is optional).",
            "Benchy compares QEDCode with the same model used on its own, judged by hidden tests.",
            f"Install it with `qed benchy install`. More: {HOMEPAGE}",
        ]
    return [
        f"Benchy {version} is installed. It runs only when you type `qed benchy ...`.",
        "Commands: qed benchy run | report | selftest | suites | share  (add --help to any).",
        "Remove it with `qed benchy uninstall`.",
    ]


def launch(args: Sequence[str]) -> int:
    """Run Benchy's own command line with the user's arguments; returns its exit code.

    The terminal is passed through so progress and Ctrl-C work as if `benchy` ran directly.
    """
    if installed_version() is None:
        print("Benchy is not installed. Install it with `qed benchy install`.", file=sys.stderr)
        return 1
    return subprocess.call([sys.executable, "-m", "benchy", *args])  # noqa: S603 - no shell
