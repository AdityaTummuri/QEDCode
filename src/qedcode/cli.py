"""The `qed` command line (Typer). Thin: it collects input and calls the engine API."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from qedcode import __version__
from qedcode.config import load_global, save_global
from qedcode.engine import api
from qedcode.engine.callbacks import NonInteractiveCallbacks
from qedcode.hardware import detect as hardware_detect
from qedcode.hardware.recommend import best_ctx_for, recommend
from qedcode.models.schemas import Grade, Speed
from qedcode.runtime.catalog import CatalogError, load_catalog, local_path
from qedcode.runtime.installer import (
    InstallDeclined,
    InstallError,
    download_model,
    remove_model,
)

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
def setup(
    reset: bool = typer.Option(False, "--reset", help="Ignore saved settings and start fresh."),
) -> None:
    """Set up QEDCode: hardware, model, helper or cloud, and agent connection."""
    from qedcode.setup_wizard import RichPrompter, run_setup

    try:
        run_setup(RichPrompter(), reset=reset)
    except (KeyboardInterrupt, EOFError) as exc:
        typer.echo("\nSetup cancelled. Nothing more was changed; run `qed setup` to resume.")
        raise typer.Exit(1) from exc


def _parse_speed(value: str) -> Speed:
    try:
        return Speed(value.lower())
    except ValueError:
        typer.echo(f"'{value}' is not a speed. Use fast, normal or sure.", err=True)
        raise typer.Exit(2) from None


def _grade_text(grade: Grade | None) -> str:
    if grade is None:
        return "[bold]Rejected[/bold]"
    return (
        f"[bold green]Grade {grade.value}[/bold green]"
        if grade is Grade.A
        else f"[bold]Grade {grade.value}[/bold]"
    )


@app.command("check")
def check_command(
    speed: Annotated[str, typer.Option("--speed", help="fast, normal or sure.")] = "normal",
    base: Annotated[str, typer.Option("--base", help="Compare with this commit.")] = "HEAD",
    as_json: Annotated[bool, typer.Option("--json", help="Print one JSON result.")] = False,
    allow_risky: Annotated[
        bool, typer.Option("--allow-risky", help="Do not stop at risky code.")
    ] = False,
) -> None:
    """Grade the changes you made yourself (uncommitted work) and write a proof receipt."""
    chosen = _parse_speed(speed)
    console = Console(stderr=True) if as_json else Console()

    def show(event) -> None:
        console.print(event.message, style="dim", markup=False, highlight=False)

    live = NonInteractiveCallbacks(on_event=None if as_json else show)
    outcome = api.check(Path.cwd(), speed=chosen, base=base, cb=live, allow_risky=allow_risky)
    if as_json:
        typer.echo(outcome.model_dump_json(indent=1, exclude={"candidate"}))
    else:
        _print_check(Console(), outcome)
    if outcome.status == "graded":
        return
    raise typer.Exit(1 if outcome.status in ("rejected", "cancelled") else 2)


def _print_check(console: Console, outcome) -> None:
    if outcome.status in ("error", "busy", "no_changes"):
        console.print(outcome.message, markup=False)
        return
    console.print()
    console.print(_grade_text(outcome.grade))
    console.print(outcome.summary or outcome.message, markup=False)
    if outcome.failing_tests:
        console.print("Failing tests:")
        for key in outcome.failing_tests[:10]:
            console.print(f"  - {key}", markup=False, highlight=False)
    if outcome.status == "rejected" and outcome.candidate is not None:
        last = outcome.candidate.checks[-1] if outcome.candidate.checks else None
        if last is not None and last.details:
            console.print(last.details[:800], markup=False, highlight=False)
    for note in outcome.notes:
        console.print(f"Note: {note}", markup=False)
    if outcome.receipt_path:
        console.print(f"Receipt: {outcome.receipt_path}", markup=False)
        console.print("Re-check it any time with: qed verify <receipt>", markup=False)


@app.command("verify")
def verify_command(
    receipt: Annotated[Path, typer.Argument(help="A receipt file (.qed/receipts/<task>.json).")],
) -> None:
    """Re-run a proof receipt and print VERIFIED or what no longer matches."""
    console = Console()
    result = api.verify(receipt, root=Path.cwd())
    if result.verified:
        from qedcode.verify.receipt import summary_line

        console.print("[bold green]VERIFIED[/bold green]")
        if result.receipt is not None:
            console.print(summary_line(result.receipt), markup=False)
        for note in result.notes:
            console.print(note, markup=False)
        return
    console.print("[bold red]NOT VERIFIED[/bold red]")
    for problem in result.mismatches:
        console.print(f"- {problem}", markup=False, highlight=False)
    raise typer.Exit(1)


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


class DownloadProgress:
    """A Rich progress bar that appears on the first update (after the user said yes)."""

    def __init__(self, console: Console) -> None:
        self._console = console
        self._progress: Progress | None = None
        self._task = None

    def __call__(self, name: str, done: int, total: int) -> None:
        if self._progress is None:
            self._progress = Progress(
                TextColumn("{task.description}"),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=self._console,
            )
            self._progress.start()
            self._task = self._progress.add_task(name, total=total or None)
        self._progress.update(self._task, completed=done, total=total or None)

    def close(self) -> None:
        if self._progress is not None:
            self._progress.stop()


def _confirmer(yes: bool):
    return lambda prompt: True if yes else typer.confirm(prompt, default=False)


@models_app.command("download")
def models_download(
    model_id: str = typer.Argument(..., help="A model id from `qed models list`."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask before downloading."),
) -> None:
    """Download a model (shows the size and asks first), then check its checksum."""
    console = Console()
    catalog = _catalog_or_exit()
    progress = DownloadProgress(console)
    try:
        entry = catalog.require(model_id)
        path = download_model(entry, _confirmer(yes), progress)
    except InstallDeclined as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    except (InstallError, CatalogError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    finally:
        progress.close()
    console.print(f"Ready: {path}")
    console.print(f"Use it with: qed models use {entry.id}")


@models_app.command("use")
def models_use(
    model: str = typer.Argument(..., help="A model id, or the path to a .gguf file."),
) -> None:
    """Make a downloaded model the one QEDCode uses."""
    console = Console()
    cfg = load_global()
    if model.lower().endswith(".gguf"):
        cfg.local.backend = "llamacpp"
        cfg.local.model = model
        save_global(cfg)
        console.print(f"QEDCode will use the model file {model}.")
        return
    entry = _catalog_or_exit().get(model)
    if entry is None:
        typer.echo(
            f"There is no model called '{model}' in the catalog. "
            "Run `qed models list` to see the choices.",
            err=True,
        )
        raise typer.Exit(2)
    cfg.local.backend = "llamacpp"
    cfg.local.model = entry.id
    hw = hardware_detect.current_hardware()
    ctx = best_ctx_for(hw, entry)
    if ctx is not None:
        cfg.local.ctx = min(cfg.local.ctx, ctx) if cfg.local.ctx else ctx
    elif hw.vram_gb > 0:
        console.print(
            f"[yellow]Warning:[/yellow] {entry.display} may not fit in {hw.vram_gb:.1f} GB. "
            "Run `qed models recommend` for a better choice."
        )
    save_global(cfg)
    console.print(f"QEDCode will use {entry.display} with a {cfg.local.ctx}-token context.")
    if not local_path(entry).exists():
        console.print(f"It is not downloaded yet: run `qed models download {entry.id}`.")


@models_app.command("remove")
def models_remove(
    model_id: str = typer.Argument(..., help="A model id from `qed models list`."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask before deleting."),
) -> None:
    """Delete a downloaded model (asks first)."""
    console = Console()
    catalog = _catalog_or_exit()
    try:
        entry = catalog.require(model_id)
        removed = remove_model(entry, _confirmer(yes))
    except InstallDeclined as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    except CatalogError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if not removed:
        console.print(f"{model_id} is not downloaded, so there is nothing to remove.")
        return
    console.print(f"Removed {entry.display}.")
    if load_global().local.model == entry.id:
        console.print("It was the model in use: pick another with `qed models use <id>`.")
