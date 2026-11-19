"""The `qed` command line (Typer). Thin: it collects input and calls the engine API."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from qedcode import __version__
from qedcode.hardware import detect as hardware_detect

app = typer.Typer(
    name="qed",
    help="QEDCode: small local models, proven fixes.",
    add_completion=False,
    no_args_is_help=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the QEDCode version and exit.",
    ),
) -> None:
    """Open QEDCode in the current folder."""
    if ctx.invoked_subcommand is None:
        typer.echo("QEDCode is not ready yet")


def hardware_table(info: hardware_detect.HardwareInfo) -> Table:
    """A Rich table showing what hardware detection found and how."""
    table = Table(title="Hardware", show_header=False, title_justify="left")
    table.add_column("what", style="bold")
    table.add_column("value")
    table.add_row("GPU vendor", info.vendor)
    table.add_row("GPU", info.gpu_name or "none found")
    table.add_row("Usable VRAM", f"{info.vram_gb:.1f} GB" if info.vram_gb else "n/a")
    table.add_row("Backend", info.backend)
    table.add_row("Driver", info.driver or "n/a")
    table.add_row("CUDA (highest supported)", info.cuda_version or "n/a")
    table.add_row("Detected by", info.source or "n/a")
    table.add_row("Notes", "\n".join(info.notes) if info.notes else "none")
    return table


@app.command()
def doctor() -> None:
    """Check this computer and show what QEDCode found."""
    console = Console()
    console.print(hardware_table(hardware_detect.detect()))
