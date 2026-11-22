"""How often do AI coding agents weaken tests? A reproducible study of public commits.

Static diff analysis only: no tests run, no model, no server. It searches public GitHub commits
whose message declares AI authorship (a co-author trailer or a "Generated with" line), downloads
each diff once, and runs QEDCode's own tamper rules (`verify.guard.weakening_in`): skips and
xfails added, asserts that can never fail, asserts removed, test files deleted.

    GITHUB_TOKEN=... uv run python scripts/study_test_weakening.py collect --per-marker 200 --out study/
    uv run python scripts/study_test_weakening.py report --data study/            # rates with 95% CIs
    uv run python scripts/study_test_weakening.py sample --data study/ --n 50     # for hand-checking

Report in aggregate: the dataset keeps repository and commit ids so anyone can re-check a row, but
the write-up never singles out a repository or a person. Hand-check a sample of flagged commits
to measure the false-positive rate before publishing any number.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qedcode.project.paths import is_test_file  # noqa: E402
from qedcode.verify.guard import parse_diff, weakening_in  # noqa: E402

API = "https://api.github.com"
# Commit-message markers that declare AI authorship; extend as agents change their trailers.
MARKERS = {
    "claude-code": '"Co-Authored-By: Claude"',
    "copilot": '"Co-authored-by: Copilot"',
    "cursor": '"Co-authored-by: Cursor Agent"',
    "aider": '"aider:"',
    "codex": '"Co-authored-by: Codex"',
}
MAX_DIFF_BYTES = 2_000_000


def _get(url: str, accept: str = "application/vnd.github+json") -> bytes:
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "qedcode-study",
    }
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=60
            ) as r:  # noqa: S310
                return r.read(MAX_DIFF_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429) and attempt < 4:  # rate limited: wait and retry
                time.sleep(60 * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"Could not fetch {url}")


def search(marker_query: str, limit: int) -> list[dict]:
    """Public commits whose message matches the marker (newest first)."""
    found: list[dict] = []
    page = 1
    while len(found) < limit and page <= 10:
        query = urllib.parse.quote(f"{marker_query} merge:false")
        data = json.loads(
            _get(f"{API}/search/commits?q={query}&sort=committer-date&per_page=100&page={page}")
        )
        items = data.get("items", [])
        if not items:
            break
        found += [{"repo": i["repository"]["full_name"], "sha": i["sha"]} for i in items]
        page += 1
        time.sleep(2.5)  # the search API allows about 30 requests a minute
    return found[:limit]


def analyse(diff: str) -> dict:
    files = parse_diff(diff)
    tests = [f.path for f in files if is_test_file(f.path) or f.path.endswith("conftest.py")]
    weakened = weakening_in(diff)
    return {
        "files": len(files),
        "test_files": len(tests),
        "touches_tests": bool(tests),
        "weakened": weakened,
        "kinds": sorted({kind for reasons in weakened.values() for kind in _kinds(reasons)}),
    }


def _kinds(reasons: list[str]) -> list[str]:
    out = []
    for reason in reasons:
        if "skip or xfail" in reason:
            out.append("skip")
        elif "can never fail" in reason:
            out.append("always-true assert")
        elif "assert line" in reason:
            out.append("asserts removed")
        elif "deleted" in reason:
            out.append("test file deleted")
    return out


def cmd_collect(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows_path = out / "commits.jsonl"
    seen = set()
    if rows_path.exists():
        seen = {json.loads(line)["sha"] for line in rows_path.read_text().splitlines() if line}
    with rows_path.open("a", encoding="utf-8") as rows:
        for marker, query in MARKERS.items():
            for hit in search(query, args.per_marker):
                if hit["sha"] in seen:
                    continue
                seen.add(hit["sha"])
                raw = _get(
                    f"{API}/repos/{hit['repo']}/commits/{hit['sha']}", "application/vnd.github.diff"
                )
                if len(raw) > MAX_DIFF_BYTES:
                    continue
                row = {"marker": marker, **hit, **analyse(raw.decode("utf-8", "replace"))}
                rows.write(json.dumps(row) + "\n")
                time.sleep(0.8)
            print(f"{marker}: done", file=sys.stderr)
    return 0


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def _rows(data: Path) -> list[dict]:
    return [json.loads(line) for line in (data / "commits.jsonl").read_text().splitlines() if line]


def cmd_report(args: argparse.Namespace) -> int:
    rows = _rows(Path(args.data))
    print(
        "| Agent marker | Commits | Touch tests | Weaken tests | Rate among test-touching (95% CI) |"
    )
    print("| --- | --- | --- | --- | --- |")
    groups = {m: [r for r in rows if r["marker"] == m] for m in MARKERS} | {"all": rows}
    for name, group in groups.items():
        touching = [r for r in group if r["touches_tests"]]
        weak = [r for r in touching if r["weakened"]]
        lo, hi = wilson(len(weak), len(touching))
        rate = f"{len(weak) / len(touching):.1%} ({lo:.1%}-{hi:.1%})" if touching else "n/a"
        print(f"| {name} | {len(group)} | {len(touching)} | {len(weak)} | {rate} |")
    kinds: dict[str, int] = {}
    for r in rows:
        for kind in r["kinds"]:
            kinds[kind] = kinds.get(kind, 0) + 1
    print("\nKinds of weakening (a commit can have several):", json.dumps(kinds))
    print("Before publishing: hand-check a sample (the `sample` command) and report the precision.")
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    rows = [r for r in _rows(Path(args.data)) if r["weakened"]]
    random.Random(args.seed).shuffle(rows)
    writer = csv.writer(sys.stdout)
    writer.writerow(["url", "reasons", "really_weakens (yes/no)", "note"])
    for r in rows[: args.n]:
        url = f"https://github.com/{r['repo']}/commit/{r['sha']}"
        writer.writerow([url, "; ".join(x for v in r["weakened"].values() for x in v), "", ""])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--per-marker", type=int, default=200)
    collect.add_argument("--out", default="study")
    collect.set_defaults(func=cmd_collect)
    report = sub.add_parser("report")
    report.add_argument("--data", default="study")
    report.set_defaults(func=cmd_report)
    sample = sub.add_parser("sample")
    sample.add_argument("--data", default="study")
    sample.add_argument("--n", type=int, default=50)
    sample.add_argument("--seed", type=int, default=0)
    sample.set_defaults(func=cmd_sample)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
