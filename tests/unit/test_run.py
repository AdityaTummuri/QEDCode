from __future__ import annotations

import sys
import textwrap
import time
from pathlib import Path

import psutil

from qedcode.verify.run import kill_process_tree, run, scrubbed_env

PY = sys.executable


def test_captures_output_and_exit_code(tmp_path: Path) -> None:
    result = run([PY, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"])
    assert result.exit_code == 3
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"
    assert not result.ok
    assert "out" in result.output and "err" in result.output
    assert result.duration_s >= 0


def test_ok_on_success_and_cwd_is_used(tmp_path: Path) -> None:
    result = run([PY, "-c", "import os; print(os.getcwd())"], cwd=tmp_path)
    assert result.ok
    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()


def test_stdin_text_is_passed() -> None:
    result = run([PY, "-c", "import sys; print(sys.stdin.read().upper())"], stdin_text="abc")
    assert result.stdout.strip() == "ABC"


def test_missing_program_is_reported_not_raised() -> None:
    result = run(["definitely-not-a-real-program-qed"])
    assert result.exit_code is None
    assert "Could not start" in result.error
    assert not result.ok


def test_timeout_kills_the_process_and_its_children(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    code = textwrap.dedent(
        f"""
        import subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        open({str(pidfile)!r}, "w").write(str(child.pid))
        time.sleep(60)
        """
    )
    started = time.monotonic()
    result = run([PY, "-c", code], timeout=1.5)
    assert result.timed_out
    assert not result.ok
    assert time.monotonic() - started < 20
    child_pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    alive = psutil.pid_exists(child_pid)
    if alive:
        try:
            alive = psutil.Process(child_pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            alive = False
    assert not alive, "the child process survived the timeout"


def test_cancel_stops_the_command() -> None:
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    started = time.monotonic()
    result = run([PY, "-c", "import time; time.sleep(60)"], timeout=60, cancelled=cancelled)
    assert result.cancelled
    assert time.monotonic() - started < 20


def test_environment_is_scrubbed_but_extra_is_kept(monkeypatch) -> None:
    monkeypatch.setenv("MY_API_KEY", "sk-test-1")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("DB_PASSWORD", "p")
    monkeypatch.setenv("SOME_SECRET", "s")
    monkeypatch.setenv("CLOUD_CREDENTIALS", "c")
    monkeypatch.setenv("HARMLESS", "ok")
    names = ("MY_API_KEY", "GITHUB_TOKEN", "DB_PASSWORD", "SOME_SECRET", "CLOUD_CREDENTIALS")
    watched = names + ("HARMLESS", "FROM_EXTRA")
    code = f"import os; print(sorted(k for k in os.environ if k in {watched!r}))"
    result = run([PY, "-c", code], env={"FROM_EXTRA": "1"})
    assert "HARMLESS" in result.stdout
    assert "FROM_EXTRA" in result.stdout
    for name in names:
        assert name not in result.stdout


def test_scrubbed_env_function() -> None:
    env = scrubbed_env({"A_KEY": "1", "path": "x", "PASSWORD": "y"}, {"EXTRA_KEY": "kept"})
    assert env == {"path": "x", "EXTRA_KEY": "kept"}


def test_log_file_is_written(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "run.log"
    result = run([PY, "-c", "print('hello log')"], log_path=log)
    assert result.log_path == str(log)
    text = log.read_text(encoding="utf-8")
    assert "hello log" in text
    assert "exit 0" in text


def test_kill_process_tree_on_missing_pid_is_safe() -> None:
    kill_process_tree(2**22 - 3)  # almost certainly not a live pid


def test_huge_output_is_trimmed_in_memory(tmp_path: Path) -> None:
    result = run([PY, "-c", "print('x' * 3_000_000)"], timeout=30)
    assert result.ok
    assert len(result.stdout) < 1_100_000
    assert "output trimmed" in result.stdout
