"""Night Shift (`qed night`, AGENTS.md section 15.13): a task queue and a morning report.

Tasks are solved one after another with quiet mode on. Nothing is applied: the report has one
card per task with the grade, the diff and the command that applies it.
"""

from __future__ import annotations

import html
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qedcode.models.schemas import SolveResult, Speed

NIGHT_DIR = Path(".qed") / "night"
MAX_FAILING = 10


@dataclass
class QueuedTask:
    request: str
    speed: str = "normal"
    added_at: str = ""


class Queue:
    """`.qed/night/queue.json`: the tasks waiting for the next `qed night run`."""

    def __init__(self, root: Path | str) -> None:
        self.path = Path(root) / NIGHT_DIR / "queue.json"

    def load(self) -> list[QueuedTask]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [
            QueuedTask(**item) for item in data if isinstance(item, dict) and item.get("request")
        ]

    def save(self, tasks: list[QueuedTask]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([t.__dict__ for t in tasks], indent=1), encoding="utf-8")

    def add(self, requests: list[str], speed: Speed) -> int:
        tasks = self.load()
        known = {t.request for t in tasks}
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        new = [r.strip() for r in requests if r.strip() and r.strip() not in known]
        tasks += [QueuedTask(r, speed.value, now) for r in dict.fromkeys(new)]
        self.save(tasks)
        return len(dict.fromkeys(new))

    def clear(self) -> int:
        count = len(self.load())
        self.save([])
        return count


def tasks_from_file(text: str) -> list[str]:
    """One task per non-empty line; lines starting with # are comments."""
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def failing_test_tasks(failing: list[str]) -> list[str]:
    return [f"Make the failing test {key} pass" for key in failing[:MAX_FAILING]]


@dataclass
class NightCard:
    request: str
    result: SolveResult
    seconds: float


@dataclass
class NightRun:
    started: str
    cards: list[NightCard] = field(default_factory=list)
    stopped_early: bool = False
    report_path: Path | None = None


def run_night(
    root: Path,
    solve_one: Callable[[str, Speed], SolveResult],
    *,
    stop: Callable[[], bool] = lambda: False,
    on_task: Callable[[int, int, str], None] | None = None,
) -> NightRun:
    """Solve every queued task in order, remove each from the queue when done, write the report."""
    queue = Queue(root)
    run = NightRun(started=time.strftime("%Y-%m-%d %H:%M"))
    tasks = queue.load()
    for index, task in enumerate(tasks, start=1):
        if stop():
            run.stopped_early = True
            break
        if on_task is not None:
            on_task(index, len(tasks), task.request)
        began = time.monotonic()
        result = solve_one(task.request, Speed(task.speed))
        run.cards.append(NightCard(task.request, result, time.monotonic() - began))
        if result.status == "cancelled":
            run.stopped_early = True
            break
        remaining = [t for t in queue.load() if t.request != task.request]
        queue.save(remaining)
    run.report_path = write_report(root, run)
    return run


def latest_report(root: Path | str) -> Path | None:
    reports = sorted((Path(root) / NIGHT_DIR).glob("report-*.html"))
    return reports[-1] if reports else None


def write_report(root: Path, run: NightRun) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = root / NIGHT_DIR / f"report-{stamp}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(run, root), encoding="utf-8")
    return path


_CSS = """
:root{--bg:#f6f7f5;--card:#fff;--ink:#1d211c;--muted:#5d665a;--line:#dfe3dc;--a:#2ea043;--b:#3b82c4;--c:#b7791f;--d:#9b2c2c}
@media (prefers-color-scheme:dark){:root{--bg:#121411;--card:#1b1e1a;--ink:#e6e9e3;--muted:#9aa396;--line:#2c3129}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:920px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 24px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:0 0 16px}
.top{display:flex;gap:12px;align-items:baseline}.seal{font-weight:700;border-radius:6px;padding:2px 10px;color:#fff;background:var(--muted)}
.A{background:var(--a)}.B{background:var(--b)}.C{background:var(--c)}.D{background:var(--d)}
.task{font-weight:600}.line{color:var(--muted);font-size:14px;margin:6px 0}pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;overflow:auto;font-size:12.5px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}details summary{cursor:pointer;color:var(--muted)}ul{margin:6px 0;padding-left:20px}
"""


def _card(card: NightCard, root: Path) -> str:
    from qedcode.verify.receipt import load_receipt, receipt_link

    e = html.escape
    result = card.result
    best = result.best
    grade = best.grade.value if best is not None and best.grade is not None else ""
    seal = f'<span class="seal {grade}">{grade or "no fix"}</span>'
    parts = [
        f'<section class="card"><div class="top">{seal}<span class="task">{e(card.request)}</span></div>'
    ]
    parts.append(f'<p class="line">{e(result.message)} · {card.seconds / 60:.1f} min</p>')
    if result.explanation:
        parts.append(f"<p>{e(result.explanation)}</p>")
    if best is not None and best.diff:
        parts.append(
            f"<details open><summary>The change</summary><pre><code>{e(best.diff)}</code></pre></details>"
        )
    if result.receipt_path and Path(result.receipt_path).is_file():
        try:
            receipt = load_receipt(result.receipt_path)
        except ValueError:
            receipt = None
        if receipt is not None:
            if receipt.mutation is not None and receipt.mutation.total:
                parts.append(
                    f'<p class="line">Proof strength {receipt.mutation.killed}/{receipt.mutation.total}</p>'
                )
            gaps = "".join(f"<li>{e(g)}</li>" for g in receipt.unverified)
            parts.append(f"<details><summary>Not proven</summary><ul>{gaps}</ul></details>")
            try:
                link = receipt_link(receipt)
                parts.append(
                    f'<p class="line"><a href="{e(link)}">Open the receipt in the Receipt Viewer</a></p>'
                )
            except ValueError:
                pass
        parts.append(f"<p>Apply it: <code>qed apply {e(result.task.id)}</code></p>")
    return "".join(parts) + "</section>"


def render_report(run: NightRun, root: Path) -> str:
    """The morning report: self-contained HTML, every piece of text escaped."""
    proven = sum(
        1
        for c in run.cards
        if c.result.best is not None
        and c.result.best.grade is not None
        and c.result.best.grade.value == "A"
    )
    summary = f"{len(run.cards)} task(s) · {proven} proven (grade A)" + (
        " · stopped early, the rest is still queued" if run.stopped_early else ""
    )
    cards = (
        "".join(_card(c, root) for c in run.cards)
        or '<section class="card">The queue was empty.</section>'
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Night Shift report</title><style>{_CSS}</style></head><body><main>"
        f'<h1>∎ Night Shift report</h1><p class="sub">{html.escape(run.started)} · {html.escape(summary)} · '
        "nothing was applied to your files</p>"
        f"{cards}</main></body></html>\n"
    )
