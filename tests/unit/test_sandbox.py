"""The sealed test box (AGENTS.md section 15.2), tested with a fake `docker` command."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qedcode.cli import app
from qedcode.config import GlobalConfig, ProjectConfig, load_project, save_project
from qedcode.engine import api, doctor, sandbox_setup
from qedcode.models.schemas import Grade, Receipt, Speed
from qedcode.runtime.fake import FakeModel
from qedcode.vcs.snapshot import snapshot
from qedcode.verify.baseline import baseline_run
from qedcode.verify.pytest_runner import run_pytest
from qedcode.verify.receipt import NETWORK_NOTE, SEALED_NOTE, load_receipt
from qedcode.verify.run import RunResult
from qedcode.verify.sandbox import (
    DockerSandbox,
    build_context,
    default_image,
    dockerfile_text,
    explain_failure,
    sandbox_for,
)
from tests.conftest import run_git
from tests.zoo_helpers import PY, script_for, zoo_git_repo

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake docker is a POSIX script")

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_docker.py"
IMAGE = "qedcode-test-box:latest"
ZOO = "001_paginate_off_by_one"


class FakeDocker:
    def __init__(self, state: Path) -> None:
        self.state = state

    def calls(self, cmd: str | None = None) -> list[dict]:
        path = self.state / "calls.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [r for r in rows if cmd is None or r["cmd"] == cmd]

    def runs(self) -> list[dict]:
        return [r for r in self.calls("run") if "refused" not in r]

    def add_image(self, name: str = IMAGE) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        path = self.state / "images.json"
        known = json.loads(path.read_text()) if path.exists() else []
        path.write_text(json.dumps([*known, name]))

    def mark(self, name: str) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / name).write_text("1")

    def unmark(self, name: str) -> None:
        (self.state / name).unlink(missing_ok=True)


@pytest.fixture
def docker(tmp_path: Path, monkeypatch) -> FakeDocker:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    script = bin_dir / "docker"
    script.write_text(f"#!{sys.executable}\n{FIXTURE.read_text()}")
    script.chmod(0o755)
    state = tmp_path / "docker_state"
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(state))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return FakeDocker(state)


def tiny_project(tmp_path: Path) -> Path:
    folder = tmp_path / "tiny"
    folder.mkdir()
    (folder / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    (folder / "test_bad.py").write_text("def test_bad():\n    assert 1 + 1 == 3\n")
    return folder


def run_dir_for(tmp_path: Path) -> Path:
    folder = tmp_path / "run"
    folder.mkdir()
    return folder


def option(run: dict, flag: str) -> list[str]:
    return run["opts"].get(flag, [])


# ------------------------------------------------------------------ the docker run command


def test_the_command_is_sealed_and_maps_paths(tmp_path: Path) -> None:
    box = DockerSandbox(image="img:1", memory="2g", cpus=1.5)
    wt, out = tmp_path / "wt", tmp_path / "out"
    inner = [
        "/usr/bin/py", "-m", "pytest", "-q", "--junitxml", str(out / "report-full.xml"),
        str(wt / "tests" / "test_a.py"), "tests/test_b.py::test_x",
    ]  # fmt: skip
    env = {"PYTHONPATH": f"{wt}/src{os.pathsep}{wt}", "PYTHONDONTWRITEBYTECODE": "1"}
    args = box.command(
        inner, worktree=wt, run_dir=out, python="/usr/bin/py", name="qed-abc", env=env
    )
    assert args[:3] == ["docker", "run", "--rm"] and "--init" in args
    pairs = {args[i]: args[i + 1] for i in range(len(args) - 1) if args[i].startswith("--")}
    assert pairs["--network"] == "none" and pairs["--pull"] == "never"
    assert pairs["--cap-drop"] == "ALL" and pairs["--security-opt"] == "no-new-privileges"
    assert (
        pairs["--memory"] == "2g" and pairs["--cpus"] == "1.5" and pairs["--pids-limit"] == "1024"
    )
    assert pairs["--name"] == "qed-abc" and pairs["--tmpfs"] == "/tmp" and "--read-only" in args
    assert (
        f"{wt}:/work" in args
        and f"{out}:/qed-out" in args
        and args[args.index("-w") + 1] == "/work"
    )
    assert "PYTHONPATH=/work/src:/work" in args and "HOME=/tmp" in args
    assert "--user" in args  # files created in the worktree stay owned by the user
    image_at = args.index("img:1")
    assert args[image_at + 1 :] == [
        "python", "-m", "pytest", "-q", "--junitxml", "/qed-out/report-full.xml",
        "/work/tests/test_a.py", "tests/test_b.py::test_x",
    ]  # fmt: skip
    assert str(wt) not in " ".join(args[image_at:]) and str(out) not in " ".join(args[image_at:])


def test_no_cpu_flag_without_a_limit(tmp_path: Path) -> None:
    args = DockerSandbox(image="i").command(
        ["py"], worktree=tmp_path, run_dir=tmp_path, python="py", name="n"
    )
    assert "--cpus" not in args and "--memory" in args


def test_sandbox_for_follows_the_project_config(tmp_path: Path) -> None:
    assert sandbox_for(ProjectConfig(), tmp_path) is None
    box = sandbox_for(
        ProjectConfig(sandbox="docker", sandbox_image="mine:1", sandbox_memory="1g"), tmp_path
    )
    assert box == DockerSandbox(image="mine:1", memory="1g", cpus=0.0)
    named = sandbox_for(ProjectConfig(sandbox="docker"), tmp_path)
    assert named is not None and named.image == default_image(tmp_path)
    assert default_image(tmp_path) != default_image(tmp_path / "other")
    assert default_image(tmp_path) == default_image(tmp_path)


def test_failure_explanations_say_what_to_do() -> None:
    box = DockerSandbox(image="img:1")
    assert "not installed" not in explain_failure(RunResult(args=[], error="x"), box)
    assert "qed sandbox off" in explain_failure(RunResult(args=[], error="x"), box)
    daemon = RunResult(
        args=[], exit_code=1, stderr="Cannot connect to the Docker daemon at unix:///x"
    )
    assert "daemon is not running" in explain_failure(daemon, box)
    missing = RunResult(args=[], exit_code=125, stderr="Unable to find image 'img:1' locally")
    assert "'img:1' is not built" in explain_failure(missing, box)
    assert "qed sandbox setup" in explain_failure(missing, box)
    assert explain_failure(RunResult(args=[], exit_code=1, stderr="1 failed"), box) == ""


# ------------------------------------------------------------------ the image


def test_the_dockerfile_installs_what_the_project_needs() -> None:
    plain = dockerfile_text("3.11", [], editable=False)
    assert plain.startswith("FROM python:3.11-slim\n") and "RUN pip install pytest" in plain
    assert "-e" not in plain and "WORKDIR /work" in plain and "COPY . /work" in plain
    full = dockerfile_text("3.12", ["requirements.txt", "requirements-dev.txt"], editable=True)
    assert "RUN pip install -r requirements.txt" in full
    assert "RUN pip install -r requirements-dev.txt" in full
    assert 'RUN pip install -e ".[dev,test]" || pip install -e .' in full


def test_the_build_context_is_a_clean_export_of_head(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    (repo / "requirements.txt").write_text("rich\n")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "deps")
    (repo / "untracked.py").write_text("x = 1\n")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "junk.txt").write_text("junk")
    snapshot(repo, "t-x")  # creates .qed state that must stay out of the export
    context = build_context(repo, tmp_path / "ctx", "3.12")
    names = {p.name for p in context.iterdir()}
    assert {"Dockerfile", ".dockerignore", "requirements.txt", "pyproject.toml", "src"} <= names
    assert ".git" not in names and ".qed" not in names and ".venv" not in names
    assert "untracked.py" not in names  # only what is committed
    text = (context / "Dockerfile").read_text()
    assert "RUN pip install -r requirements.txt" in text and "pip install -e" in text


# ------------------------------------------------------------------ running pytest through it


def test_pytest_runs_through_the_container_and_the_report_is_read(tmp_path: Path, docker) -> None:
    docker.add_image()
    project, run_dir = tiny_project(tmp_path), run_dir_for(tmp_path)
    outcome = run_pytest(
        project, PY, ["test_ok.py"], 60, run_dir=run_dir, sandbox=DockerSandbox(image=IMAGE)
    )
    assert outcome.status == "ok" and outcome.passed_keys == ["test_ok::test_ok"]
    (run,) = docker.runs()
    assert option(run, "--network") == ["none"] and option(run, "--pull") == ["never"]
    assert run["image"] == IMAGE and run["command"][:3] == ["python", "-m", "pytest"]
    assert "/qed-out/report-targeted.xml" in run["command"] and "test_ok.py" in run["command"]
    assert option(run, "-w") == ["/work"]
    # the recorded command is the host-form one, exactly as without a sandbox
    assert (
        outcome.check.command[0] == PY
        and str(run_dir / "report-targeted.xml") in outcome.check.command
    )


def test_failures_inside_the_container_are_reported_like_normal(tmp_path: Path, docker) -> None:
    docker.add_image()
    outcome = run_pytest(
        tiny_project(tmp_path), PY, None, 60, run_dir=run_dir_for(tmp_path),
        sandbox=DockerSandbox(image=IMAGE),
    )  # fmt: skip
    assert outcome.status == "failed" and [r.key for r in outcome.failing] == ["test_bad::test_bad"]


def test_without_a_sandbox_docker_is_never_called(tmp_path: Path, docker) -> None:
    outcome = run_pytest(
        tiny_project(tmp_path), PY, ["test_ok.py"], 60, run_dir=run_dir_for(tmp_path)
    )
    assert outcome.status == "ok" and docker.calls() == []


def test_docker_not_running_says_how_to_fix_it(tmp_path: Path, docker) -> None:
    docker.add_image()
    docker.mark("down")
    outcome = run_pytest(
        tiny_project(tmp_path), PY, ["test_ok.py"], 60, run_dir=run_dir_for(tmp_path),
        sandbox=DockerSandbox(image=IMAGE),
    )  # fmt: skip
    assert outcome.status == "error"
    assert "The Docker daemon is not running" in outcome.check.summary
    assert "qed sandbox off" in outcome.check.summary


def test_docker_missing_on_the_path(tmp_path: Path, monkeypatch) -> None:
    box = DockerSandbox(image=IMAGE, docker=str(tmp_path / "no-such-docker"))
    outcome = run_pytest(
        tiny_project(tmp_path), PY, ["test_ok.py"], 60, run_dir=run_dir_for(tmp_path), sandbox=box
    )
    assert outcome.status == "error" and "Docker could not be started" in outcome.check.summary


def test_a_missing_image_is_never_pulled_and_names_the_fix(tmp_path: Path, docker) -> None:
    outcome = run_pytest(
        tiny_project(tmp_path), PY, ["test_ok.py"], 60, run_dir=run_dir_for(tmp_path),
        sandbox=DockerSandbox(image="never-built:1"),
    )  # fmt: skip
    assert outcome.status == "error"
    assert "'never-built:1' is not built" in outcome.check.summary
    assert "qed sandbox setup" in outcome.check.summary
    assert [option(r, "--pull") for r in docker.runs()] == [["never"]]


def test_a_timeout_removes_the_container(tmp_path: Path, docker) -> None:
    docker.add_image()
    project = tmp_path / "slow"
    project.mkdir()
    (project / "test_slow.py").write_text("import time\n\ndef test_slow():\n    time.sleep(60)\n")
    started = time.monotonic()
    outcome = run_pytest(
        project, PY, None, 2, run_dir=run_dir_for(tmp_path), sandbox=DockerSandbox(image=IMAGE)
    )
    assert outcome.status == "timeout" and time.monotonic() - started < 30
    (run,) = docker.runs()
    (removed,) = docker.calls("rm")
    assert removed["name"] == option(run, "--name")[0] and "-f" in removed["argv"]


def test_a_cancel_removes_the_container(tmp_path: Path, docker) -> None:
    docker.add_image()
    project = tmp_path / "slow"
    project.mkdir()
    (project / "test_slow.py").write_text("import time\n\ndef test_slow():\n    time.sleep(60)\n")
    ticks = iter(range(1000))
    outcome = run_pytest(
        project, PY, None, 60, run_dir=run_dir_for(tmp_path), sandbox=DockerSandbox(image=IMAGE),
        cancelled=lambda: next(ticks) > 12,
    )  # fmt: skip
    assert outcome.status == "cancelled" and len(docker.calls("rm")) == 1


# ------------------------------------------------------------------ the engine end to end


def sealed_repo(tmp_path: Path, docker: FakeDocker) -> Path:
    repo = zoo_git_repo(tmp_path)
    docker.add_image()
    save_project(
        repo, ProjectConfig(python=PY, confirmed=True, sandbox="docker", sandbox_image=IMAGE)
    )
    return repo


def test_the_baseline_runs_in_the_container_and_reports_when_it_cannot(
    tmp_path: Path, docker
) -> None:
    repo = sealed_repo(tmp_path, docker)
    project = load_project(repo)
    commit = snapshot(repo, "t-base")
    ok = baseline_run(
        repo, commit, project, full=True, related=[], run_dir=tmp_path / "rd", name="b1"
    )
    assert ok.baseline.runnable and docker.runs()
    docker.mark("down")
    down = baseline_run(
        repo, commit, project, full=True, related=[], run_dir=tmp_path / "rd2", name="b2"
    )
    assert not down.baseline.runnable and "qed sandbox off" in down.baseline.reason


def test_a_full_solve_in_the_sealed_box_reaches_grade_a_with_the_right_receipt(
    tmp_path: Path, docker
) -> None:
    repo = sealed_repo(tmp_path, docker)
    result = api.solve(
        repo,
        "paginate drops the last item",
        config=GlobalConfig(),
        client=FakeModel(script_for(ZOO)),
    )
    assert result.status == "done" and result.best.grade is Grade.A
    runs = docker.runs()
    assert len(runs) >= 4  # baseline, repro check, candidate checks, full suite
    assert all(option(r, "--network") == ["none"] for r in runs)
    assert all(r["image"] == IMAGE for r in runs)
    receipt = load_receipt(result.receipt_path)
    assert receipt.sandbox == "docker"
    assert SEALED_NOTE in receipt.unverified and NETWORK_NOTE not in receipt.unverified
    host_commands = [c for c in receipt.commands]
    assert host_commands and all(c[0] == "{python}" for c in host_commands)  # same shape as before


def test_verify_reruns_a_sealed_receipt_in_the_sealed_box(tmp_path: Path, docker) -> None:
    repo = sealed_repo(tmp_path, docker)
    result = api.solve(
        repo,
        "paginate drops the last item",
        config=GlobalConfig(),
        client=FakeModel(script_for(ZOO)),
    )
    before = len(docker.runs())
    outcome = api.verify(result.receipt_path, repo)
    assert outcome.verified, outcome.mismatches
    assert len(docker.runs()) > before and not outcome.notes


def test_verify_says_so_when_the_box_is_off_here(tmp_path: Path, docker) -> None:
    repo = sealed_repo(tmp_path, docker)
    result = api.solve(
        repo,
        "paginate drops the last item",
        config=GlobalConfig(),
        client=FakeModel(script_for(ZOO)),
    )
    sandbox_setup.turn_off(repo)
    calls_before = len(docker.runs())
    outcome = api.verify(result.receipt_path, repo)
    assert outcome.verified and len(docker.runs()) == calls_before
    assert any("sealed test box" in note and "qed sandbox setup" in note for note in outcome.notes)


def test_a_normal_receipt_keeps_the_network_note(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    result = api.solve(
        repo,
        "paginate drops the last item",
        config=GlobalConfig(),
        client=FakeModel(script_for(ZOO)),
    )
    receipt = load_receipt(result.receipt_path)
    assert receipt.sandbox == "" and NETWORK_NOTE in receipt.unverified


def test_old_receipts_without_the_field_still_load(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    result = api.solve(
        repo,
        "paginate drops the last item",
        config=GlobalConfig(),
        client=FakeModel(script_for(ZOO)),
    )
    data = json.loads(Path(result.receipt_path).read_text())
    data.pop("sandbox")
    assert Receipt.model_validate(data).sandbox == ""


# ------------------------------------------------------------------ qed sandbox setup / status / off


def project_repo(tmp_path: Path) -> Path:
    return zoo_git_repo(tmp_path)


def test_status_off_ready_and_broken(tmp_path: Path, docker) -> None:
    repo = project_repo(tmp_path)
    off = sandbox_setup.status(repo)
    assert off.mode == "off" and "off" in off.describe() and not off.ready
    save_project(
        repo, ProjectConfig(python=PY, confirmed=True, sandbox="docker", sandbox_image=IMAGE)
    )
    missing = sandbox_setup.status(repo)
    assert (
        missing.docker_ready
        and not missing.image_built
        and "qed sandbox setup" in missing.describe()
    )
    docker.add_image()
    assert sandbox_setup.status(repo).ready
    docker.mark("down")
    down = sandbox_setup.status(repo)
    assert not down.docker_ready and "not usable" in down.describe()


def test_setup_asks_first_builds_checks_and_turns_the_box_on(
    tmp_path: Path, docker, monkeypatch
) -> None:
    repo = project_repo(tmp_path)
    prompts: list[str] = []
    steps: list[str] = []
    result = sandbox_setup.setup(repo, lambda p: prompts.append(p) or True, steps.append)
    assert result.ok and result.image == default_image(repo)
    (prompt,) = prompts
    assert "python:3." in prompt and "about 150 MB" in prompt and "needs internet" in prompt
    assert "Tests afterwards run with the network off" in prompt
    (build,) = docker.calls("build")
    assert build["tag"] == default_image(repo) and build["dockerfile"].startswith("FROM python:3.")
    assert "RUN pip install pytest" in build["dockerfile"] and "Dockerfile" in build["files"]
    assert docker.runs() and option(docker.runs()[-1], "--network") == ["none"]  # the smoke test
    saved = load_project(repo)
    assert saved.sandbox == "docker" and saved.sandbox_image == default_image(repo)
    assert any("Building" in s for s in steps) and sandbox_setup.status(repo).ready


def test_setup_declined_downloads_nothing(tmp_path: Path, docker) -> None:
    repo = project_repo(tmp_path)
    result = sandbox_setup.setup(repo, lambda p: False)
    assert not result.ok and "Nothing was downloaded" in result.message
    assert docker.calls("build") == [] and load_project(repo).sandbox == "off"


def test_setup_mentions_a_base_image_that_is_already_here(
    tmp_path: Path, docker, monkeypatch
) -> None:
    repo = project_repo(tmp_path)
    version = sandbox_setup.python_version_for(repo, PY)
    monkeypatch.setenv("FAKE_DOCKER_BASE", f"python:{version}-slim")
    prompts: list[str] = []
    sandbox_setup.setup(repo, lambda p: prompts.append(p) or False)
    assert "already on this computer" in prompts[0] and "about 150 MB" not in prompts[0]


def test_setup_without_docker_running_does_not_ask(tmp_path: Path, docker) -> None:
    repo = project_repo(tmp_path)
    docker.mark("down")
    asked: list[str] = []
    result = sandbox_setup.setup(repo, lambda p: asked.append(p) or True)
    assert not result.ok and "Docker is installed but not running" in result.message and not asked


def test_a_failed_build_leaves_the_box_off_and_points_to_the_log(tmp_path: Path, docker) -> None:
    repo = project_repo(tmp_path)
    docker.mark("build_fails")
    result = sandbox_setup.setup(repo, lambda p: True)
    assert not result.ok and "The Docker build failed" in result.message
    assert "build.log" in result.message and "qed sandbox setup" in result.message
    assert (repo / ".qed" / "sandbox" / "build.log").exists()
    assert load_project(repo).sandbox == "off"


def test_turn_off(tmp_path: Path, docker) -> None:
    repo = project_repo(tmp_path)
    assert sandbox_setup.turn_off(repo) is False
    sandbox_setup.setup(repo, lambda p: True)
    assert sandbox_setup.turn_off(repo) is True and load_project(repo).sandbox == "off"


def test_the_cli_commands(tmp_path: Path, docker, monkeypatch) -> None:
    repo = project_repo(tmp_path)
    monkeypatch.chdir(repo)
    runner = CliRunner()
    assert "sealed test box is off" in runner.invoke(app, ["sandbox"]).output
    declined = runner.invoke(app, ["sandbox", "setup"], input="n\n")
    assert declined.exit_code == 1 and "Nothing was downloaded" in declined.output
    built = runner.invoke(app, ["sandbox", "setup", "--yes"])
    assert built.exit_code == 0 and "is ready" in built.output and "needs internet" in built.output
    assert "on and ready" in runner.invoke(app, ["sandbox", "status"]).output
    assert "is off: tests run on this computer" in runner.invoke(app, ["sandbox", "off"]).output
    assert "already off" in runner.invoke(app, ["sandbox", "off"]).output
    docker.mark("down")
    failed = runner.invoke(app, ["sandbox", "setup", "--yes"])
    assert failed.exit_code == 1 and "not running" in failed.output


# ------------------------------------------------------------------ doctor


def test_the_doctor_check(tmp_path: Path, docker) -> None:
    repo = project_repo(tmp_path)
    assert doctor.check_sandbox(repo).status == "ok" and "off" in doctor.check_sandbox(repo).detail
    save_project(
        repo, ProjectConfig(python=PY, confirmed=True, sandbox="docker", sandbox_image=IMAGE)
    )
    missing = doctor.check_sandbox(repo)
    assert (
        missing.status == "fail"
        and "not built" in missing.detail
        and "qed sandbox setup" in missing.fix
    )
    docker.add_image()
    assert doctor.check_sandbox(repo).status == "ok"
    docker.mark("down")
    down = doctor.check_sandbox(repo)
    assert down.status == "fail" and "Docker is not usable" in down.detail and down.fix
    assert Speed.NORMAL  # keep the import used
