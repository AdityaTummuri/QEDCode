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
from qedcode.config import load_global, logs_dir, save_global
from qedcode.engine import api
from qedcode.engine.callbacks import NonInteractiveCallbacks
from qedcode.engine.events import event_line
from qedcode.hardware import detect as hardware_detect
from qedcode.hardware.recommend import best_ctx_for, recommend
from qedcode.models.schemas import Event, Grade, SolveResult, Speed
from qedcode.runtime.catalog import CatalogError, load_catalog, local_path
from qedcode.runtime.client import ModelError, make_client
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
    from qedcode.logging_setup import setup_logging

    setup_logging()  # tracebacks and details go to ~/.qedcode/logs/qedcode.log, not the screen
    if ctx.invoked_subcommand is None:
        from qedcode.tui.app import run_tui

        run_tui(Path.cwd())


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


def _print_solve(console: Console, result: SolveResult) -> None:
    console.print()
    if result.status == "done" and result.best is not None:
        console.print(_grade_text(result.best.grade))
        console.print(result.message, markup=False)
        if result.explanation:
            console.print()
            console.print(result.explanation, markup=False)

        def describe(c) -> str:
            return f"{c.id}: " + (
                f"grade {c.grade.value}" if c.grade else f"failed at {c.failed_stage}"
            )

        tries = ", ".join(describe(c) for c in result.candidates)
        console.print(f"\nTries: {tries}", markup=False)
        if result.receipt_path:
            console.print(f"Receipt: {result.receipt_path}", markup=False)
        if "Applied to" not in result.message:
            console.print(f"Apply it with: qed apply {result.task.id}", markup=False)
        return
    console.print(result.message, markup=False)
    if result.question:
        console.print(f"Question for you: {result.question}", markup=False)


@app.command("solve")
def solve_command(
    task: Annotated[str, typer.Argument(help="What to do, in plain words.")],
    speed: Annotated[str | None, typer.Option("--speed", help="fast, normal or sure.")] = None,
    apply_flag: Annotated[
        bool, typer.Option("--apply", help="Apply the result if its grade is A or B.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print one JSON result.")] = False,
    allow_risky: Annotated[
        bool, typer.Option("--allow-risky", help="Do not stop at risky code.")
    ] = False,
) -> None:
    """Fix or build something: several tries, tests on each one, one proven result."""
    config = load_global()
    chosen = _parse_speed(speed) if speed else config.engine.speed
    console = Console(stderr=True) if as_json else Console()

    def show(event: Event) -> None:
        line = event_line(event, include_result=False)
        if line and not as_json:
            console.print(line, style="dim", markup=False, highlight=False)

    callbacks = NonInteractiveCallbacks(on_event=show)
    try:
        client = make_client(config, "main")
    except ModelError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    try:
        with console.status("Working...", spinner="dots") if not as_json else _no_status():
            result = api.solve(
                Path.cwd(),
                task,
                speed=chosen,
                cb=callbacks,
                config=config,
                client=client,
                allow_risky=allow_risky,
                apply=apply_flag,
            )
    except Exception as exc:  # noqa: BLE001 - last resort: a friendly message, details in the log
        _log_crash(exc)
        typer.echo(
            "Something went wrong inside QEDCode. The details were saved to "
            f"{logs_dir() / 'qedcode.log'}. Please report it.",
            err=True,
        )
        raise typer.Exit(3) from exc
    finally:
        if config.engine.unload_on_exit:
            try:
                client.unload()
            except Exception:  # noqa: BLE001 - unloading is best effort
                pass
    if as_json:
        typer.echo(result.model_dump_json(indent=1))
    else:
        _print_solve(Console(), result)
    if result.status == "done":
        return
    raise typer.Exit(2 if result.status == "error" else 1)


class _no_status:  # noqa: N801 - a tiny context manager standing in for console.status
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


def _log_crash(exc: Exception) -> None:
    import logging
    import traceback

    from qedcode.logging_setup import setup_logging

    setup_logging()
    logging.getLogger("qedcode.cli").error(
        "Unhandled error\n%s", "".join(traceback.format_exception(exc))
    )


@app.command("apply")
def apply_command(
    task_id: Annotated[
        str | None, typer.Argument(help="A task id (default: the last result).")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask about a low grade.")] = False,
) -> None:
    """Apply a finished result to your files (undo it any time with `qed undo`)."""
    console = Console()
    outcome = api.apply_result(Path.cwd(), task_id)
    if outcome.status == "needs_confirmation":
        console.print(outcome.message, markup=False)
        if yes or typer.confirm("Apply it anyway?", default=False):
            outcome = api.apply_result(Path.cwd(), task_id, allow_low_grade=True)
        else:
            raise typer.Exit(1)
    console.print(outcome.message, markup=False)
    if outcome.status != "applied":
        raise typer.Exit(1 if outcome.status == "refused" else 2)


@app.command("undo")
def undo_command() -> None:
    """Take the last applied change back out of your files."""
    outcome = api.undo(Path.cwd())
    Console().print(outcome.message, markup=False)
    if outcome.status != "undone":
        raise typer.Exit(1 if outcome.status == "refused" else 2)


@app.command("mcp")
def mcp_command() -> None:
    """Run the MCP server over stdio, so Claude Code, OpenCode and other agents can use QEDCode."""
    from qedcode.mcp_server import run_stdio

    run_stdio()


memory_app = typer.Typer(
    help="What QEDCode remembers about this project (fixes you applied).",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(memory_app, name="memory")


@memory_app.callback()
def memory_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        memory_list_command()


@memory_app.command("list")
def memory_list_command() -> None:
    """Show the fixes QEDCode remembers and uses as examples."""
    console = Console()
    lessons = api.memory_list()
    if not lessons:
        console.print(
            "Nothing is remembered yet. After you apply a fix with grade A or B, QEDCode "
            "remembers it and shows it to the model as an example next time.",
            markup=False,
        )
        return
    table = Table(title="Remembered fixes", title_justify="left")
    for column in ("#", "grade", "task", "files"):
        table.add_column(column)
    for lesson in lessons:
        table.add_row(
            str(lesson.id), lesson.grade, lesson.request[:60], ", ".join(lesson.files)[:60]
        )
    console.print(table)
    console.print("Forget one with `qed memory forget <#>`, all with `qed memory clear`.")


@memory_app.command("forget")
def memory_forget_command(
    number: Annotated[int, typer.Argument(help="The # from the list.")],
) -> None:
    """Forget one remembered fix."""
    if not api.memory_forget(number):
        typer.echo(
            f"There is no remembered fix number {number}. Run `qed memory` to see them.", err=True
        )
        raise typer.Exit(1)
    Console().print(f"Forgot fix {number}.", markup=False)


@memory_app.command("clear")
def memory_clear_command(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask first.")] = False,
) -> None:
    """Forget everything remembered in this project."""
    count = len(api.memory_list())
    if count == 0:
        Console().print("Nothing is remembered.", markup=False)
        return
    if not yes and not typer.confirm(f"Forget all {count} remembered fix(es)?", default=False):
        raise typer.Exit(1)
    api.memory_clear()
    Console().print(f"Forgot {count} fix(es).", markup=False)


sandbox_app = typer.Typer(
    help="The sealed test box: run your tests in Docker with the network off.",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(sandbox_app, name="sandbox")


@sandbox_app.callback()
def sandbox_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        sandbox_status_command()


@sandbox_app.command("status")
def sandbox_status_command() -> None:
    """Show whether the sealed test box is on and ready."""
    Console().print(api.sandbox_status().describe(), markup=False)


@sandbox_app.command("setup")
def sandbox_setup_command(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask first.")] = False,
) -> None:
    """Build the sealed test box (asks first: it downloads an image and your dependencies)."""
    console = Console()

    def ask(prompt: str) -> bool:
        console.print(prompt, markup=False)
        return True if yes else typer.confirm("Build it?", default=False)

    result = api.sandbox_setup(
        confirm=ask, on_progress=lambda text: console.print(text, markup=False)
    )
    console.print(result.message, markup=False)
    if not result.ok:
        raise typer.Exit(1)


@sandbox_app.command("off")
def sandbox_off_command() -> None:
    """Run tests on this computer again."""
    if api.sandbox_off():
        Console().print("The sealed test box is off: tests run on this computer.", markup=False)
    else:
        Console().print("The sealed test box was already off.", markup=False)


@app.command("uninstall")
def uninstall_command(
    everything: Annotated[
        bool,
        typer.Option("--all", help="Also remove ~/.qedcode (engines, models, logs, config, keys)."),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask first.")] = False,
) -> None:
    """Remove the project's .qed/ folder (and with --all, everything QEDCode installed)."""
    from qedcode.engine import uninstall as uninstall_module

    console = Console()
    root = api.resolve_root()

    def ask(prompt: str) -> bool:
        console.print(prompt, markup=False)
        return True if yes else typer.confirm("Remove these?", default=False)

    if everything:
        done = uninstall_module.uninstall_all(root, ask)
    else:
        done = uninstall_module.uninstall_project(root, ask)
    if not done:
        console.print("Nothing was removed.", markup=False)
        raise typer.Exit(1)
    for line in done:
        console.print(line, markup=False)


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
def doctor(
    update_engine: Annotated[
        bool,
        typer.Option("--update-engine", help="Move to the newest llama.cpp build (asks first)."),
    ] = False,
) -> None:
    """Check this computer, the project and the model, and say how to fix what is wrong."""
    from qedcode.engine import doctor as doctor_module

    console = Console()
    config = load_global()
    hw = hardware_detect.current_hardware()
    if update_engine:
        try:
            message = doctor_module.update_engine(
                config, hw, lambda prompt: typer.confirm(prompt, default=False)
            )
        except InstallDeclined as exc:
            console.print(str(exc), markup=False)
            raise typer.Exit(1) from exc
        except InstallError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
        if message.startswith("Updated"):
            save_global(config)
        console.print(message, markup=False)
    checks = doctor_module.run_doctor(api.resolve_root(), config, hw=hw)
    table = Table(title="QEDCode doctor", title_justify="left")
    for column in ("", "check", "what was found"):
        table.add_column(column, no_wrap=column != "what was found")
    marks = {"ok": "ok", "warn": "warn", "fail": "FAIL"}
    for check in checks:
        table.add_row(marks[check.status], check.name, check.detail)
    console.print(table)
    problems = [c for c in checks if c.status != "ok"]
    for check in problems:
        console.print(f"[bold]{check.name}[/bold]: {check.fix}", highlight=False)
    ok, warnings, failures = doctor_module.summarize(checks)
    console.print(f"\n{ok} ok, {warnings} warning(s), {failures} problem(s).", markup=False)
    if failures:
        raise typer.Exit(1)


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
