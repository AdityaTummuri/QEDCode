"""The QEDCode Check action's helper: turn a `qed check --json` result into a PR comment.

Standard library only (it runs on the runner's own python3). Two jobs:

    report.py render --result check.json [--link-file link.txt] [--fail-below B] --out DIR
        writes DIR/comment.md and DIR/pr.json and prints GitHub step outputs
    report.py post --dir DIR
        creates or updates the one sticky comment on the pull request

`post` never runs pull request code, so it is safe in a `workflow_run` job for PRs from forks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

MARKER = "<!-- qedcode-check -->"
RANKS = {"A": 4, "B": 3, "C": 2, "D": 1}
VIEWER = "https://adityatummuri.github.io/QEDCode/receipt/"
MAX_COMMENT = 20_000


def render_comment(result: dict, link: str = "", run_url: str = "") -> str:
    """The sticky comment for one check result (Markdown)."""
    status = result.get("status", "error")
    grade = result.get("grade")
    lines = [MARKER]
    if status == "graded" and grade:
        lines.append(f"### ∎ QEDCode: Grade {grade}")
        lines.append("")
        lines.append(result.get("summary") or result.get("message") or "")
        step = result.get("next_step") or ""
        if step:
            lines += ["", f"**To raise the grade:** {step}"]
    elif status == "rejected":
        lines.append("### ∎ QEDCode: change rejected")
        lines += ["", result.get("message") or "The change failed a check."]
    elif status == "no_changes":
        lines.append("### ∎ QEDCode: nothing to check")
        lines += ["", result.get("message") or "The pull request changes nothing."]
    else:
        lines.append("### ∎ QEDCode: the check could not run")
        lines += ["", result.get("message") or "See the job log for details."]
    failing = result.get("failing_tests") or []
    if failing:
        lines += ["", "Failing tests:"]
        lines += [f"- `{key}`" for key in failing[:10]]
        if len(failing) > 10:
            lines.append(f"- ... and {len(failing) - 10} more")
    for note in result.get("notes") or []:
        lines += ["", f"> {note}"]
    proof = []
    if link:
        proof.append(f"[Open the proof in the Receipt Viewer]({link})")
    if run_url:
        proof.append(f"[receipt and log]({run_url}) (artifact `qedcode-check`)")
    if result.get("receipt_path"):
        proof.append("re-run it locally with `qed verify <receipt>`")
    if proof:
        lines += ["", " · ".join(proof)]
    lines += [
        "",
        "<sub>Graded by [QEDCode](https://github.com/AdityaTummuri/QEDCode): "
        "deterministic checks, no model.</sub>",
    ]
    return "\n".join(lines) + "\n"


def below(grade: str | None, floor: str) -> bool:
    """True when `grade` is lower than `floor` (no grade counts as lowest; '' or 'none' = never)."""
    floor = (floor or "").strip().upper()
    if floor in ("", "NONE"):
        return False
    if floor not in RANKS:
        raise ValueError(f"fail-below must be A, B, C, D or none, not '{floor}'.")
    return RANKS.get((grade or "").upper(), 0) < RANKS[floor]


def _outputs(pairs: dict[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    text = "".join(f"{k}<<__QED__\n{v}\n__QED__\n" for k, v in pairs.items())
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)


def cmd_render(args: argparse.Namespace) -> int:
    try:
        result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result = {"status": "error", "message": f"qed check printed no result ({exc})."}
    link = ""
    if args.link_file and Path(args.link_file).is_file():
        link = Path(args.link_file).read_text(encoding="utf-8").strip()
        if not link.startswith(VIEWER):
            link = ""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "comment.md").write_text(render_comment(result, link, args.run_url), encoding="utf-8")
    pr = {"number": args.pr or "", "head_sha": args.head_sha or ""}
    (out / "pr.json").write_text(json.dumps(pr), encoding="utf-8")
    try:
        failed = below(result.get("grade"), args.fail_below)
    except ValueError as exc:
        print(f"::error::{exc}")
        return 2
    if result.get("status") not in ("graded", "no_changes"):
        failed = failed or (args.fail_below or "none").strip().lower() != "none"
    _outputs(
        {
            "grade": result.get("grade") or "",
            "status": result.get("status") or "error",
            "summary": result.get("summary") or result.get("message") or "",
            "next-step": result.get("next_step") or "",
            "receipt-link": link,
            "failed": "true" if failed else "false",
        }
    )
    return 0


def _api(method: str, path: str, token: str, body: dict | None = None) -> object:
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    request = urllib.request.Request(
        f"{base}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed host
        return json.loads(response.read() or b"null")


def cmd_post(args: argparse.Namespace) -> int:
    folder = Path(args.dir)
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    body = (folder / "comment.md").read_text(encoding="utf-8")
    pr = json.loads((folder / "pr.json").read_text(encoding="utf-8"))
    number = str(pr.get("number", ""))
    if not number.isdigit() or not token or not repo:
        print("No pull request number or token: the comment is not posted.")
        return 0
    if not body.startswith(MARKER):
        print("::error::The comment file is not a QEDCode comment; not posting it.")
        return 1
    if len(body) > MAX_COMMENT:  # a PR's own tests ran on that runner: never trust the size
        body = body[:MAX_COMMENT] + "\n\n(cut: the full result is in the run's artifact)\n"
    try:
        expected = os.environ.get("QED_EXPECTED_HEAD_SHA", "")
        if expected:  # workflow_run: the artifact must belong to the PR it names
            head = _api("GET", f"/repos/{repo}/pulls/{number}", token)["head"]["sha"]  # type: ignore[index]
            if head != expected:
                print("::error::The pull request moved on or the artifact names another PR.")
                return 1
        comments = _api("GET", f"/repos/{repo}/issues/{number}/comments?per_page=100", token)
        mine = [c for c in comments or [] if MARKER in (c.get("body") or "")]  # type: ignore[union-attr]
        if mine:
            _api("PATCH", f"/repos/{repo}/issues/comments/{mine[0]['id']}", token, {"body": body})
        else:
            _api("POST", f"/repos/{repo}/issues/{number}/comments", token, {"body": body})
    except urllib.error.HTTPError as exc:
        print(
            f"::warning::Could not post the QEDCode comment ({exc.code}). A pull request from a "
            "fork has a read-only token: add the comment workflow from the QEDCode docs."
        )
        return 0
    print("Posted the QEDCode comment.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    render = sub.add_parser("render")
    render.add_argument("--result", required=True)
    render.add_argument("--link-file", default="")
    render.add_argument("--fail-below", default="none")
    render.add_argument("--out", required=True)
    render.add_argument("--pr", default="")
    render.add_argument("--head-sha", default="")
    render.add_argument("--run-url", default="")
    render.set_defaults(func=cmd_render)
    post = sub.add_parser("post")
    post.add_argument("--dir", required=True)
    post.set_defaults(func=cmd_post)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
