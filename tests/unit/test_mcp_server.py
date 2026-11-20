from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

from qedcode.config import GlobalConfig
from qedcode.engine.lock import project_lock
from qedcode.mcp_server import JobRunner, QedMcpService, build_server, solve_payload
from qedcode.models.schemas import Mode, SolveResult, TaskSpec
from qedcode.runtime.fake import FakeModel
from tests.conftest import BUGZOO_DIR, tree_state
from tests.tui_helpers import SlowModel, wait_for
from tests.zoo_helpers import script_for, zoo_git_repo

ZOO = "001_paginate_off_by_one"
TASK = "paginate drops the last item"
PAGES = "src/shop/pages.py"
RISKY = (
    f"EDIT search_replace path={PAGES}\n<<<<<<< SEARCH\n    end = start + size - 1\n=======\n"
    "    import subprocess\n    end = start + size\n>>>>>>> REPLACE\n"
)


def service_for(model: FakeModel, config: GlobalConfig | None = None) -> QedMcpService:
    cfg = config or GlobalConfig()
    return QedMcpService(client_factory=lambda c: model, config_loader=lambda: cfg)


def structured(result) -> dict:
    """The dict a tool returned, from an MCP CallToolResult."""
    if getattr(result, "structured_content", None):
        return result.structured_content
    return json.loads(result.content[0].text)


async def finished(service: QedMcpService, job_id: str) -> dict:
    out = await service.status(job_id)
    while out["status"] == "running":
        await asyncio.sleep(0.2)
        out = await service.status(job_id)
    return out


# ------------------------------------------------------------------ solve


async def test_solve_returns_the_proven_result(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    out = await service_for(FakeModel(script_for(ZOO))).solve(TASK, project_dir=str(repo))
    assert out["status"] == "done" and out["grade"] == "A" and out["job_id"]
    assert out["summary"].startswith("Grade A · repro test failed before, passes now")
    assert out["explanation"].startswith("paginate computed the end of each page")
    assert "+    end = start + size\n" in out["diff"]
    assert Path(out["receipt_path"]).exists() and out["question"] is None


async def test_solve_does_not_touch_files_unless_apply_is_set(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    before = tree_state(repo)
    service = service_for(FakeModel(script_for(ZOO)))
    await service.solve(TASK, project_dir=str(repo))
    assert tree_state(repo) == before
    applied = await service_for(FakeModel(script_for(ZOO))).solve(
        TASK, apply=True, project_dir=str(repo)
    )
    assert "Applied to" in applied["summary"]
    assert "start + size\n" in (repo / "src" / "shop" / "pages.py").read_text()


async def test_a_long_solve_returns_running_and_status_follows_it(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    slow = SlowModel(script_for(ZOO))
    service = service_for(slow)
    out = await service.solve(TASK, wait_seconds=0, project_dir=str(repo))
    assert out["status"] == "running" and out["job_id"]
    job_id = out["job_id"]
    await wait_for(lambda: bool(service.runner.get(job_id).events))
    mid = await service.status(job_id)
    assert mid["status"] == "running" and mid["progress"] and mid["seconds"] >= 0
    final = await finished(service, job_id)
    assert final["status"] == "done" and final["grade"] == "A" and final["job_id"] == job_id
    assert any(line.startswith("Finding files") for line in final["progress"])


async def test_a_second_call_while_busy_returns_busy(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    service = service_for(SlowModel(script_for(ZOO)))
    first = await service.solve(TASK, wait_seconds=0, project_dir=str(repo))
    second = await service.solve(TASK, wait_seconds=0, project_dir=str(repo))
    assert second["status"] == "busy" and second["job_id"] == first["job_id"]
    assert "already running" in second["message"]
    checked = await service.check(project_dir=str(repo))
    assert checked["status"] == "busy"
    await finished(service, first["job_id"])
    third = await service_for(FakeModel(script_for(ZOO))).solve(TASK, project_dir=str(repo))
    assert third["status"] == "done"  # the project is free again


async def test_a_task_running_in_another_process_is_busy_too(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    with project_lock(repo, "t-terminal"):
        out = await service_for(FakeModel(script_for(ZOO))).solve(TASK, project_dir=str(repo))
    assert out["status"] == "busy" and "running in this project" in out["message"]


async def test_bad_arguments_are_explained(tmp_path: Path) -> None:
    service = service_for(FakeModel({}))
    assert "speed must be" in (await service.solve(TASK, speed="warp"))["message"]
    assert "Say what to do" in (await service.solve("  "))["message"]
    assert "speed must be" in (await service.check(speed="warp"))["message"]
    assert "no job called 'nope'" in (await service.status("nope"))["message"]
    assert (await service.status("nope"))["status"] == "error"


async def test_risky_code_is_rejected_because_nobody_can_approve_it(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    script = script_for(ZOO, coder=[RISKY] * 3, repairer=[RISKY])
    out = await service_for(FakeModel(script)).solve(TASK, project_dir=str(repo))
    assert out["status"] == "no_fix" and out["grade"] is None and out["diff"] == ""
    assert "closest attempt" in out["summary"] and out["question"]


async def test_the_helper_is_never_used_without_pre_approval(tmp_path: Path, monkeypatch) -> None:
    repo = zoo_git_repo(tmp_path)
    helper = FakeModel({"coder": [script_for(ZOO)["coder"][0]]}, name="fake:helper")
    monkeypatch.setattr("qedcode.engine.api.make_client", lambda cfg, role="main": helper)
    cfg = GlobalConfig()
    cfg.engine.mode = Mode.MIX
    cfg.helper.enabled, cfg.helper.model = True, "x/y"  # ask_before_use stays true
    stuck = script_for(ZOO, coder=[RISKY] * 3, repairer=[RISKY])
    out = await service_for(FakeModel(stuck), cfg).solve(TASK, project_dir=str(repo))
    assert out["status"] == "no_fix" and helper.calls == []
    cfg.helper.ask_before_use = False  # now the config pre-approves it
    out2 = await service_for(FakeModel(stuck), cfg).solve(TASK, project_dir=str(repo))
    assert out2["status"] == "done" and helper.calls


async def test_model_errors_come_back_as_an_error_status(tmp_path: Path) -> None:
    from qedcode.runtime.client import ModelError

    class Down(FakeModel):
        def chat(self, *args, **kwargs):
            raise ModelError("QEDCode could not reach the model server.")

    repo = zoo_git_repo(tmp_path)
    out = await service_for(Down({})).solve(TASK, project_dir=str(repo))
    assert out["status"] == "error" and "could not reach the model server" in out["message"]


# ------------------------------------------------------------------ progress notifications


class FakeContext:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[float, object, str]] = []
        self.fail = fail

    async def report_progress(self, progress, total=None, message=None) -> None:
        if self.fail:
            raise RuntimeError("the client does not support progress")
        self.calls.append((progress, total, message))


async def test_progress_is_reported_while_waiting(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    ctx = FakeContext()
    out = await service_for(SlowModel(script_for(ZOO))).solve(TASK, project_dir=str(repo), ctx=ctx)
    assert out["status"] == "done"
    assert ctx.calls and [c[0] for c in ctx.calls] == sorted(c[0] for c in ctx.calls)
    assert any("Finding files" in c[2] or "Taking a snapshot" in c[2] for c in ctx.calls)


async def test_a_client_without_progress_support_does_not_break_the_call(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    out = await service_for(SlowModel(script_for(ZOO))).solve(
        TASK, project_dir=str(repo), ctx=FakeContext(fail=True)
    )
    assert out["status"] == "done"


# ------------------------------------------------------------------ check and verify


async def test_check_grades_changes_you_made(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    patch = (BUGZOO_DIR / ZOO / "solution.patch").read_bytes()
    subprocess.run(["git", "apply", "-"], cwd=repo, input=patch, check=True)
    service = service_for(FakeModel({}))
    out = await service.check(project_dir=str(repo))
    assert out["status"] == "graded" and out["grade"] == "B" and out["failing_tests"] == []
    assert out["summary"].startswith("Grade B") and Path(out["receipt_path"]).exists()
    (repo / "src" / "shop" / "pages.py").write_text("def broken(:\n")
    bad = await service.check(project_dir=str(repo))
    assert bad["status"] == "rejected" and bad["grade"] is None and "parse" in bad["summary"]


async def test_check_with_nothing_changed(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    out = await service_for(FakeModel({})).check(project_dir=str(repo))
    assert out["status"] == "no_changes" and "nothing to check" in out["message"]


async def test_verify_a_receipt_and_a_tampered_one(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    service = service_for(FakeModel(script_for(ZOO)))
    solved = await service.solve(TASK, project_dir=str(repo))
    ok = await service.verify(solved["receipt_path"])
    assert ok["verified"] is True and ok["mismatches"] == []
    path = Path(solved["receipt_path"])
    data = json.loads(path.read_text())
    data["patch"] = data["patch"].replace("size", "SIZE", 1)
    path.write_text(json.dumps(data))
    bad = await service.verify(str(path))
    assert bad["verified"] is False and "checksum" in bad["mismatches"][0]


# ------------------------------------------------------------------ housekeeping


async def test_shutdown_cancels_a_running_job(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    slow = SlowModel(script_for(ZOO))
    slow.delay = 0.6
    service = service_for(slow)
    out = await service.solve(TASK, wait_seconds=0, project_dir=str(repo))
    await wait_for(lambda: bool(service.runner.get(out["job_id"]).events))
    service.shutdown()
    final = await finished(service, out["job_id"])
    assert final["status"] == "cancelled"
    assert not (repo / ".qed" / "lock").exists()


def test_the_idle_watcher_is_not_started_when_a_client_is_injected() -> None:
    service = service_for(FakeModel({}))
    service._ensure_idle_unload(GlobalConfig())
    assert service._watcher_started is False


def test_solve_payload_mapping() -> None:
    busy = SolveResult(
        status="error",
        task=TaskSpec(id="t", request="x"),
        message="Another QEDCode task is already running in this project.",
    )
    assert solve_payload(busy, "j1")["status"] == "busy"
    err = SolveResult(status="error", task=TaskSpec(id="t", request="x"), message="boom")
    assert solve_payload(err)["status"] == "error" and solve_payload(err)["question"] is None
    ask = SolveResult(
        status="needs_input", task=TaskSpec(id="t", request="x"), message="m", question="Which?"
    )
    assert solve_payload(ask)["question"] == "Which?"


def test_the_runner_reports_a_crashing_job_as_an_error(tmp_path: Path) -> None:
    runner = JobRunner()

    def boom(job):
        raise ValueError("kaboom")

    job = runner.start("solve", tmp_path, boom)
    assert job.done.wait(5)
    assert job.result["status"] == "error" and "ValueError" in job.result["message"]
    again = runner.start("solve", tmp_path, lambda j: {"status": "done"})  # the slot was freed
    assert again.done.wait(5)


def test_a_crashing_job_is_logged_with_its_traceback(tmp_path: Path, caplog) -> None:
    runner = JobRunner()

    def boom(job):
        raise ValueError("kaboom")

    with caplog.at_level("ERROR", logger="qedcode.mcp"):
        job = runner.start("solve", tmp_path, boom)
        assert job.done.wait(5)
    assert "qedcode.log" in job.result["message"]
    assert any(r.exc_info and "kaboom" in str(r.exc_info[1]) for r in caplog.records)


def test_finished_jobs_do_not_pile_up_forever(tmp_path: Path, monkeypatch) -> None:
    import qedcode.mcp_server as mcp_server

    monkeypatch.setattr(mcp_server, "KEEP_FINISHED_JOBS", 3)
    runner = JobRunner()
    ids = []
    for _ in range(6):
        job = runner.start("check", tmp_path, lambda j: {"status": "graded"})
        assert job.done.wait(5)
        ids.append(job.id)
    kept = [i for i in ids if runner.get(i) is not None]
    assert ids[-1] in kept and len(kept) <= 4  # the newest finished ones (plus the latest)
    assert runner.get(ids[0]) is None


# ------------------------------------------------------------------ through the real protocol


async def test_the_server_lists_the_four_tools_with_their_arguments() -> None:
    async with Client(build_server(service_for(FakeModel({})))) as client:
        listing = await client.list_tools()
        tools = {t.name: t for t in listing.tools}
        assert set(tools) == {"solve", "check", "verify", "status"}
        solve = tools["solve"].input_schema
        assert solve["required"] == ["task"]
        assert set(solve["properties"]) == {"task", "speed", "apply", "wait_seconds", "project_dir"}
        assert solve["properties"]["wait_seconds"]["default"] == 600
        assert set(tools["check"].input_schema["properties"]) == {"speed", "base", "project_dir"}
        assert set(tools["verify"].input_schema["properties"]) == {"receipt_path"}
        assert set(tools["status"].input_schema["properties"]) == {"job_id"}
        assert "poll" in tools["solve"].description and "grade" in tools["solve"].description
        assert "poll `status`" in (client.instructions or "")


async def test_solve_through_the_protocol_with_progress_notifications(tmp_path: Path) -> None:
    repo = zoo_git_repo(tmp_path)
    seen: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        seen.append((progress, total, message))

    service = service_for(SlowModel(script_for(ZOO)))
    async with Client(build_server(service)) as client:
        result = await client.call_tool(
            "solve", {"task": TASK, "project_dir": str(repo)}, progress_callback=on_progress
        )
        out = structured(result)
        assert out["status"] == "done" and out["grade"] == "A"
        status = structured(await client.call_tool("status", {"job_id": out["job_id"]}))
        assert status["status"] == "done" and status["job_id"] == out["job_id"]
        unknown = structured(await client.call_tool("status", {"job_id": "nope"}))
        assert unknown["status"] == "error"
        verified = structured(
            await client.call_tool("verify", {"receipt_path": out["receipt_path"]})
        )
        assert verified["verified"] is True
    assert seen, "no progress notification reached the client"
    assert [s[0] for s in seen] == sorted(s[0] for s in seen)  # progress only goes up
    assert any(m and "Taking a snapshot" in m for _, _, m in seen)


# ------------------------------------------------------------------ the real stdio transport


async def test_qed_mcp_speaks_mcp_over_stdio_and_prints_nothing_else(tmp_path: Path) -> None:
    home = tmp_path / "home"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from qedcode.cli import app; app(['mcp'])"],
        env={"QEDCODE_HOME": str(home), "PATH": __import__("os").environ["PATH"]},
    )
    async with Client(params) as client:
        listing = await client.list_tools()
        assert {t.name for t in listing.tools} == {"solve", "check", "verify", "status"}
        out = structured(await client.call_tool("status", {"job_id": "nope"}))
        assert out["status"] == "error" and "no job called" in out["message"]


async def test_check_returns_the_notes(tmp_path: Path) -> None:
    """Notes say which Python was used and why tests could not run; agents need them too."""
    repo = zoo_git_repo(tmp_path)
    patch = (BUGZOO_DIR / ZOO / "solution.patch").read_bytes()
    subprocess.run(["git", "apply", "-"], cwd=repo, input=patch, check=True)
    out = await service_for(FakeModel({})).check(project_dir=str(repo))
    assert isinstance(out["notes"], list)


def test_the_instructions_name_both_languages() -> None:
    from qedcode.mcp_server import INSTRUCTIONS

    assert "Python or JavaScript/TypeScript" in INSTRUCTIONS and "notes" in INSTRUCTIONS
