"""The `qed` command line (Typer). Thin: it collects input and calls the engine API."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from qedcode import __version__
from qedcode.hardware import detect as hardware_detect
from qedcode.hardware.recommend import recommend
from qedcode.runtime.catalog import CatalogError, load_catalog, local_path

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


models_app = typer.Typer(
    help="List, recommend and manage local models.", invoke_without_command=True
)
app.add_typer(models_app, name="models")


@models_app.callback()
def models_main(ctx: typer.Context) -> None:
    """Show the model catalog when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        models_list()


def _catalog_or_exit():
    try:
        return load_catalog()
    except CatalogError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


@models_app.command("list")
def models_list() -> None:
    """Show every model QEDCode knows, and which are downloaded."""
    catalog = _catalog_or_exit()
    table = Table(title="Models")
    for column in ("id", "model", "size", "license", "downloaded"):
        table.add_column(column, no_wrap=column == "id")
    for entry in catalog.by_rank():
        size = f"{entry.weights_gb:.2f} GB" if entry.weights_gb is not None else "unverified"
        have = "yes" if local_path(entry).exists() else "no"
        table.add_row(entry.id, entry.display, size, entry.license, have)
    Console().print(table)


@models_app.command("recommend")
def models_recommend() -> None:
    """Recommend the best model and context size for this computer."""
    catalog = _catalog_or_exit()
    hw = hardware_detect.current_hardware()
    rec = recommend(hw, catalog)
    console = Console()
    console.print(hardware_table(hw))
    if not rec.model_id:
        console.print(rec.reason)
        raise typer.Exit(2)
    console.print(f"[bold]Recommended:[/bold] {rec.display} ({rec.model_id})")
    console.print(f"Context: {rec.ctx} tokens, KV cache {rec.kv_cache}, runs on the {rec.run_on}")
    console.print(rec.reason)
    for warning in rec.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")
