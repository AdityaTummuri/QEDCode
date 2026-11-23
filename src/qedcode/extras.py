"""Optional parts of QEDCode (AGENTS.md 15.16): imported on first use, never installed by it."""

from __future__ import annotations

import importlib
from types import ModuleType


class MissingExtra(RuntimeError):  # noqa: N818 - reads as a fact in `except MissingExtra`
    """An optional part is not installed. The message names the command that adds it."""


def load_extra(module: str, extra: str, what: str) -> ModuleType:
    """Import `module` or raise MissingExtra saying how to install the `extra` that holds it."""
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".", 1)[0] != module.split(".", 1)[0]:
            raise  # the module is there but broken: hiding that would mislead
        raise MissingExtra(
            f"{what} needs an optional part of QEDCode that is not installed. Add it with "
            f'`uv tool install "qedcode[{extra}]"` (or `pip install "qedcode[{extra}]"`), '
            "then run the command again."
        ) from exc
