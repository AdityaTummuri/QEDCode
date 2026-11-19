"""Run git through the one subprocess helper (verify/run.py): no shell, no user config."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from qedcode.verify.run import RunResult, run

# QEDCode's own commits (snapshots, candidates) never depend on, or change, the user's git identity.
IDENTITY = ["-c", "user.name=QEDCode", "-c", "user.email=qedcode@localhost"]
SAFE_CONFIG = ["-c", "commit.gpgsign=false", "-c", "core.quotepath=off", "-c", "gc.auto=0"]
GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
DEFAULT_TIMEOUT_S = 120.0


class GitError(RuntimeError):
    """A git command failed. The message says what happened, why, and what to do next."""


def git(
    root: Path | str,
    *args: str,
    env: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    stdin_text: str | None = None,
    identity: bool = False,
    exact: bool = False,
) -> RunResult:
    """Run `git <args>` in `root` and return the RunResult (never raises)."""
    argv = ["git", *SAFE_CONFIG, *(IDENTITY if identity else []), *args]
    return run(
        argv,
        cwd=root,
        timeout=timeout,
        env={**GIT_ENV, **(env or {})},
        stdin_text=stdin_text,
        exact=exact,
    )


def git_out(
    root: Path | str,
    *args: str,
    env: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    stdin_text: str | None = None,
    identity: bool = False,
    strip: bool = True,
    exact: bool = False,
) -> str:
    """Run git and return stdout; raise GitError with a plain-English message on failure."""
    result = git(
        root, *args, env=env, timeout=timeout, stdin_text=stdin_text, identity=identity, exact=exact
    )
    if not result.ok:
        raise GitError(explain(args, result, root))
    return result.stdout.strip() if strip else result.stdout


def explain(args: Sequence[str], result: RunResult, root: Path | str) -> str:
    """Turn a failed git run into a message for humans."""
    name = args[0] if args else "git"
    if result.error:
        return (
            f"QEDCode needs git but could not run it ({result.error}). "
            "Install git (https://git-scm.com) and make sure the `git` command works."
        )
    if result.timed_out:
        return (
            f"`git {name}` took too long in {root} and was stopped. A very large folder or a "
            "locked repository can do this; check `git status` in that folder and try again."
        )
    detail = (result.stderr or result.stdout).strip().splitlines()
    last = detail[-1][:200] if detail else "no message"
    return (
        f"`git {name}` failed in {root} (exit {result.exit_code}): {last}. "
        "Check that this folder is a healthy git repository (run `git status` there)."
    )


def is_git_repo(path: Path | str) -> bool:
    """True when `path` is inside a git work tree."""
    result = git(path, "rev-parse", "--is-inside-work-tree")
    return result.ok and result.stdout.strip() == "true"


def repo_root(path: Path | str) -> Path:
    """The top folder of the repository containing `path`."""
    out = git_out(path, "rev-parse", "--show-toplevel")
    return Path(out)


def has_commits(root: Path | str) -> bool:
    """False for a repository with no commits yet (an unborn HEAD)."""
    return git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}").ok


def head_commit(root: Path | str) -> str | None:
    result = git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    return result.stdout.strip() if result.ok else None


def git_dir(root: Path | str) -> Path:
    """The absolute .git folder (for the common dir in worktrees, the main one)."""
    out = git_out(root, "rev-parse", "--git-common-dir")
    path = Path(out)
    return path if path.is_absolute() else (Path(root) / path).resolve()
