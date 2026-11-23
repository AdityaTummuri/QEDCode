"""How often do AI agents' pull requests weaken existing tests? Experiment E1, on the AIDev dataset.

Static analysis of published patches only: no code runs, no model, nothing is sent anywhere.
Data: AIDev v3 (https://huggingface.co/datasets/hao-li/AIDev, CC-BY-4.0), tables pull_request and
pr_commit_details, downloaded by hand into one folder. pyarrow reads them and is not a QEDCode
dependency:

    uv run --with pyarrow python scripts/study_aidev.py collect --data aidev/ --out study/
    uv run python scripts/study_aidev.py report --out study/
    uv run python scripts/study_aidev.py sample --out study/ --pattern skip_existing --n 25

A pull request's commits are added up per file, and a line that one commit adds and a later one
removes counts as neither. Four patterns are kept apart because they differ in how often they are a
real weakening; only a pattern whose hand-checked precision reaches 90% may be published.

    skip_existing   a skip or xfail put on a test that already existed
    always_true     an assertion replaced by one that can never fail
    asserts_gone    a test file that ends up with fewer assertions and no fewer tests
    file_deleted    a test file deleted while no test file was added

Report in aggregate only: no repository or person is singled out.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from study_test_weakening import wilson  # noqa: E402

from qedcode.project.paths import is_js_path, is_test_file  # noqa: E402
from qedcode.verify import guard  # noqa: E402

PATTERNS = ("skip_existing", "always_true", "asserts_gone", "file_deleted")
# Reasons that make a skip an ordinary, honest condition rather than a way around a failure.
_CONDITIONAL = re.compile(r"skipif|skipIf|skipUnless|importorskip|runIf|skipIf\(", re.IGNORECASE)
_PY_TEST = re.compile(r"^\s*(?:async\s+)?def\s+test|^\s*class\s+Test")
_JS_TEST = re.compile(r"\b(?:it|test|describe)(?:\.\w+)?\s*\(\s*(['\"`])(?P<name>.*?)\1")
_JS_OFF = re.compile(
    r"(?:\b(?:it|test|describe)\.(?:skip|todo)|\bx(?:it|test|describe))\s*\(\s*(['\"`])(?P<name>.*?)\1"
)


def is_test_path(path: str) -> bool:
    return is_test_file(path)


def lines_of(patch: str) -> list[tuple[str, str]]:
    """(sign, text) for every line of a GitHub API patch; a hunk header gives ("@", "")."""
    out = []
    for raw in patch.split("\n"):
        if raw.startswith("@@"):
            out.append(("@", ""))
        elif raw[:1] in ("+", "-", " "):
            out.append((raw[0], raw[1:].rstrip("\r")))
    return out


def _skips_on_existing_tests(lines: list[tuple[str, str]], js: bool) -> list[str]:
    found = []
    if js:
        before = {m.group("name") for s, t in lines if s == "-" for m in [_JS_TEST.search(t)] if m}
        for sign, text in lines:
            match = _JS_OFF.search(text) if sign == "+" else None
            if match and match.group("name") in before:
                found.append(text.strip())
        return found
    for i, (sign, text) in enumerate(lines):
        if sign != "+" or text.lstrip().startswith("#") or not guard._SKIP.search(text):
            continue
        if _CONDITIONAL.search(text):
            continue
        if text.lstrip().startswith("@"):
            # a decorator: the next line that is not another added decorator or its arguments
            for next_sign, next_text in lines[i + 1 : i + 12]:
                if _PY_TEST.match(next_text) and next_sign != "-":
                    if next_sign == " ":
                        found.append(text.strip())
                    break
                if next_sign == "@":
                    break
    return found


def _removed_then_always_true(lines: list[tuple[str, str]], js: bool) -> list[str]:
    assertion = guard._JS_ASSERT if js else guard._ASSERT
    always = guard._JS_ALWAYS_TRUE if js else guard._ALWAYS_TRUE
    found, removed = [], False
    for sign, text in lines:
        if sign == "@":
            removed = False
        elif sign == "-" and assertion.match(text) and not always.search(text):
            removed = True
        elif sign == "+" and removed and always.search(text):
            found.append(text.strip())
    return found


def weigh(files: dict[str, dict]) -> dict[str, list[str]]:
    """The patterns one pull request shows, with the evidence, from its per-file patches."""
    found: dict[str, list[str]] = {}
    added_tests = any(is_test_path(p) and f["status"] == "added" for p, f in files.items())
    for path, f in files.items():
        if not is_test_path(path):
            continue
        js = is_js_path(path)
        if f["status"] == "removed":
            if not added_tests and len(f["lines"]) > 0:
                found.setdefault("file_deleted", []).append(path)
            continue
        if f["status"] != "modified":
            continue
        lines = f["lines"]
        plus = collections.Counter(t.strip() for s, t in lines if s == "+")
        minus = collections.Counter(t.strip() for s, t in lines if s == "-")
        for pattern, hits in (
            ("skip_existing", _skips_on_existing_tests(lines, js)),
            ("always_true", _removed_then_always_true(lines, js)),
        ):
            kept = [h for h in hits if plus[h] > minus[h]]
            if kept:
                found.setdefault(pattern, []).append(f"{path}: {kept[0][:100]}")
        assertion = guard._JS_ASSERT if js else guard._ASSERT
        test = _JS_TEST if js else _PY_TEST
        net_asserts = sum(c for t, c in plus.items() if assertion.match(t)) - sum(
            c for t, c in minus.items() if assertion.match(t)
        )
        net_tests = sum(c for t, c in plus.items() if test.search(t)) - sum(
            c for t, c in minus.items() if test.search(t)
        )
        if net_asserts < 0 and net_tests >= 0:
            found.setdefault("asserts_gone", []).append(f"{path}: {net_asserts} assertion(s)")
    return found


def group_files(rows: list[dict]) -> dict[str, dict]:
    """Add up one pull request's commits per file (rows in commit order)."""
    files: dict[str, dict] = {}
    for r in rows:
        path = r["filename"]
        if not path:
            continue
        f = files.setdefault(path, {"status": r["status"], "lines": [], "commits": 0})
        f["commits"] += 1
        if f["status"] == "added" and r["status"] == "modified":
            pass  # created by this pull request: still new
        elif f["status"] == "added" and r["status"] == "removed":
            f["status"] = "gone"
        else:
            f["status"] = r["status"]
        f["lines"] += [("@", "")] + lines_of(r["patch"] or "")
    return files


def cmd_collect(args: argparse.Namespace) -> int:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    data, out = Path(args.data), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    prs = pq.read_table(
        data / "pull_request.parquet", columns=["id", "agent", "html_url", "merged_at"]
    )
    info = {r["id"]: r for r in prs.to_pylist()}
    table = pq.read_table(
        data / "pr_commit_details.parquet",
        columns=["pr_id", "sha", "message", "filename", "status", "patch"],
    )
    table = table.filter(pc.is_valid(table["filename"]))
    groups: dict[int, list[dict]] = {}
    for batch in table.to_batches(max_chunksize=20_000):
        for r in batch.to_pylist():
            if str(r["message"] or "").startswith("Merge "):
                continue  # a merge commit brings in other people's changes
            if not (is_test_path(r["filename"])):
                r["patch"] = ""
            groups.setdefault(r["pr_id"], []).append(r)
    with (out / "prs.jsonl").open("w", encoding="utf-8") as handle:
        for pr_id, rows in groups.items():
            pr = info.get(pr_id)
            if pr is None:
                continue
            files = group_files(rows)
            tests = [p for p in files if is_test_path(p)]
            existing = [p for p in tests if files[p]["status"] in ("modified", "removed")]
            handle.write(
                json.dumps(
                    {
                        "pr": pr_id,
                        "agent": pr["agent"],
                        "url": pr["html_url"],
                        "merged": bool(pr["merged_at"]),
                        "code": any(p.endswith(".py") or is_js_path(p) for p in files),
                        "source": any(
                            (p.endswith(".py") or is_js_path(p)) and not is_test_path(p)
                            for p in files
                        ),
                        "touches_tests": bool(tests),
                        "touches_existing_tests": bool(existing),
                        "found": weigh(files),
                    }
                )
                + "\n"
            )
    return 0


def _rows(out: str) -> list[dict]:
    return [json.loads(x) for x in (Path(out) / "prs.jsonl").read_text().splitlines() if x]


def _rate(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k / n:.2%} ({lo:.2%}-{hi:.2%})" if n else "n/a"


def cmd_report(args: argparse.Namespace) -> int:
    rows = [r for r in _rows(args.out) if r["code"]]
    agents = sorted({r["agent"] for r in rows})
    print("Pull requests that change Python or JavaScript/TypeScript. Rates are among those that")
    print("change an existing test file, with 95% Wilson intervals.\n")
    print("| Agent | PRs | change existing tests | " + " | ".join(PATTERNS) + " |")
    print("| --- | --- | --- |" + " --- |" * len(PATTERNS))
    for name in [*agents, "all"]:
        group = [r for r in rows if name == "all" or r["agent"] == name]
        base = [r for r in group if r["touches_existing_tests"]]
        cells = [_rate(sum(1 for r in base if p in r["found"]), len(base)) for p in PATTERNS]
        print(f"| {name} | {len(group)} | {len(base)} | " + " | ".join(cells) + " |")
    print("\n| Agent | PRs that change non-test code | of those, change or add no test |")
    print("| --- | --- | --- |")
    for name in [*agents, "all"]:
        group = [r for r in rows if r["source"] and (name == "all" or r["agent"] == name)]
        none = sum(1 for r in group if not r["touches_tests"])
        print(f"| {name} | {len(group)} | {_rate(none, len(group))} |")
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    rows = [r for r in _rows(args.out) if r["code"] and args.pattern in r["found"]]
    random.Random(args.seed).shuffle(rows)
    writer = csv.writer(sys.stdout)
    writer.writerow(["url", "evidence", "really_weakens (yes/no)", "note"])
    for r in rows[: args.n]:
        writer.writerow([r["url"], "; ".join(r["found"][args.pattern][:3]), "", ""])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--data", required=True)
    collect.add_argument("--out", default="study")
    collect.set_defaults(func=cmd_collect)
    report = sub.add_parser("report")
    report.add_argument("--out", default="study")
    report.set_defaults(func=cmd_report)
    sample = sub.add_parser("sample")
    sample.add_argument("--out", default="study")
    sample.add_argument("--pattern", choices=PATTERNS, required=True)
    sample.add_argument("--n", type=int, default=25)
    sample.add_argument("--seed", type=int, default=0)
    sample.set_defaults(func=cmd_sample)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
