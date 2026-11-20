from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from qedcode.runtime.client import ModelError
from qedcode.runtime.llamacpp import LlamaCppClient, resolve_model_path
from qedcode.runtime.server_manager import (
    ServerFlags,
    ServerManager,
    ServerSpec,
    ServerStartError,
    build_server_args,
    free_port,
    parse_help_flags,
    probe_flags,
)
from tests.conftest import REPO_ROOT

FAKE = REPO_ROOT / "tests" / "fixtures" / "fake_llama_server.py"


def fake_children() -> list[psutil.Process]:
    out = []
    for child in psutil.Process().children(recursive=True):
        try:
            if "fake_llama_server.py" in " ".join(child.cmdline()):
                out.append(child)
        except psutil.Error:
            pass
    return out


@pytest.fixture
def model_file(tmp_path: Path) -> Path:
    path = tmp_path / "models" / "tiny.gguf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"GGUF not a real model")
    return path


@pytest.fixture
def make_spec(model_file: Path, tmp_path: Path):
    def _make(**overrides) -> ServerSpec:
        env = {
            "FAKE_LLAMA_ARGS_FILE": str(tmp_path / "args.txt"),
            "FAKE_LLAMA_REQUEST_FILE": str(tmp_path / "request.json"),
        }
        env.update(overrides.pop("env", {}))
        base = dict(
            model_id="tiny",
            model_path=model_file,
            launcher=[sys.executable, str(FAKE)],
            ctx=4096,
            kv_cache="q8_0",
            ngl=99,
            env=env,
            flags_cache=tmp_path / "flags.json",
        )
        base.update(overrides)
        return ServerSpec(**base)

    return _make


@pytest.fixture
def manager():
    mgr = ServerManager()
    yield mgr
    mgr.stop_idle_watcher()
    mgr.stop()
    for proc in fake_children():  # belt and braces: never leave a server behind
        proc.kill()
    mgr.unregister_client()


# ------------------------------------------------------------------ pure helpers


def test_free_port_is_usable() -> None:
    port = free_port()
    assert 1024 <= port <= 65535


def test_parse_help_flags_value_and_bool_styles() -> None:
    value = parse_help_flags("  -fa, --flash-attn [on|off|auto]  set\n  -ctk, --cache-type-k T\n")
    assert value.has("--flash-attn") and value.has("-ctk")
    assert value.flash_attn_takes_value is True
    boolean = parse_help_flags("  -fa, --flash-attn   enable Flash Attention\n")
    assert boolean.has("--flash-attn")
    assert boolean.flash_attn_takes_value is False
    assert not parse_help_flags("nothing useful").has("--jinja")


def test_build_args_full_and_degraded(tmp_path: Path) -> None:
    spec = ServerSpec("m", tmp_path / "m.gguf", ["llama-server"], ctx=8192, kv_cache="q8_0")
    full = ServerFlags({"--jinja", "--flash-attn", "-ctk"}, flash_attn_takes_value=True)
    args = build_server_args(spec, 5555, full)
    assert args[:3] == ["llama-server", "-m", str(tmp_path / "m.gguf")]
    assert args[args.index("--port") + 1] == "5555"
    assert args[args.index("-c") + 1] == "8192"
    assert args[args.index("-ngl") + 1] == "99"
    assert args[args.index("--parallel") + 1] == "1"
    assert "--jinja" in args
    assert args[args.index("--flash-attn") + 1] == "on"
    assert args[args.index("-ctk") + 1] == "q8_0"
    assert args[args.index("-ctv") + 1] == "q8_0"
    assert args[args.index("--host") + 1] == "127.0.0.1"

    boolean = build_server_args(spec, 1, ServerFlags({"--flash-attn", "-ctk"}))
    assert "--flash-attn" in boolean and "on" not in boolean

    no_fa = build_server_args(spec, 1, ServerFlags({"--jinja"}))
    assert "--flash-attn" not in no_fa and "-ctk" not in no_fa  # KV types only with flash attention

    f16 = ServerSpec("m", tmp_path / "m.gguf", ["x"], kv_cache="f16")
    assert "-ctk" not in build_server_args(f16, 1, full)

    cpu = ServerSpec("m", tmp_path / "m.gguf", ["x"], ngl=0)
    cpu_args = build_server_args(cpu, 1, full)
    assert cpu_args[cpu_args.index("-ngl") + 1] == "0"
    assert "--flash-attn" not in cpu_args


def test_probe_flags_runs_help_once_and_caches(tmp_path: Path) -> None:
    counter = tmp_path / "count.txt"
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys, pathlib\n"
        f"p = pathlib.Path({str(counter)!r})\n"
        "p.write_text(str(int(p.read_text() or 0) + 1) if p.exists() else '1')\n"
        "print('  --jinja  x\\n  -fa, --flash-attn [on|off|auto]  y')\n",
        encoding="utf-8",
    )
    cache = tmp_path / "flags.json"
    first = probe_flags([sys.executable, str(script)], cache)
    second = probe_flags([sys.executable, str(script)], cache)
    assert first.has("--jinja") and second.has("--jinja")
    assert second.flash_attn_takes_value is True
    assert counter.read_text() == "1"  # the second call came from the cache


# ------------------------------------------------------------------ manager


def test_ensure_starts_a_healthy_server_and_records_state(manager, make_spec) -> None:
    spec = make_spec()
    messages: list[str] = []
    info = manager.ensure(spec, on_status=messages.append)
    assert manager.is_healthy(info)
    assert info.model_id == "tiny" and info.ctx == 4096
    state = json.loads(manager.state_path.read_text(encoding="utf-8"))
    assert state["pid"] == info.pid and state["port"] == info.port
    assert "Loading model..." in messages
    assert psutil.pid_exists(info.pid)
    args = Path(spec.env["FAKE_LLAMA_ARGS_FILE"]).read_text(encoding="utf-8").split("\n")
    assert "--jinja" in args
    assert args[args.index("--flash-attn") + 1] == "on"
    assert args[args.index("-ctk") + 1] == "q8_0"
    assert manager.live_clients() == [os.getpid()]


def test_a_second_ensure_reuses_the_same_server(manager, make_spec) -> None:
    spec = make_spec()
    first = manager.ensure(spec)
    second = ServerManager().ensure(spec)
    assert (second.pid, second.port) == (first.pid, first.port)
    assert len(fake_children()) == 1


def test_two_clients_starting_at_once_start_only_one_server(manager, make_spec) -> None:
    spec = make_spec(env={"FAKE_LLAMA_DELAY": "1.0"})
    results: list = []

    def start() -> None:
        results.append(ServerManager().ensure(spec))

    threads = [threading.Thread(target=start) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(results) == 3
    assert len({r.pid for r in results}) == 1
    assert len(fake_children()) == 1


def test_a_different_model_replaces_the_server(manager, make_spec, tmp_path: Path) -> None:
    first = manager.ensure(make_spec())
    other = tmp_path / "models" / "other.gguf"
    other.write_bytes(b"x")
    second = manager.ensure(make_spec(model_id="other", model_path=other))
    assert second.pid != first.pid
    assert second.model_id == "other"
    deadline = time.monotonic() + 5
    while psutil.pid_exists(first.pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert len(fake_children()) == 1


def test_stop_kills_the_server_and_clears_state(manager, make_spec) -> None:
    info = manager.ensure(make_spec())
    assert manager.stop() is True
    assert not manager.state_path.exists()
    deadline = time.monotonic() + 5
    while psutil.pid_exists(info.pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not fake_children()
    assert manager.stop() is False  # nothing left to stop


def test_a_dead_server_in_the_state_file_is_cleaned_up(manager, make_spec) -> None:
    info = manager.ensure(make_spec())
    psutil.Process(info.pid).kill()
    deadline = time.monotonic() + 5
    while psutil.pid_exists(info.pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert manager.info() is None
    fresh = manager.ensure(make_spec())
    assert fresh.pid != info.pid
    assert manager.is_healthy(fresh)


def test_loading_state_is_waited_for(manager, make_spec) -> None:
    started = time.monotonic()
    info = manager.ensure(make_spec(env={"FAKE_LLAMA_DELAY": "1.5"}))
    assert time.monotonic() - started >= 1.0
    assert manager.is_healthy(info)


def test_out_of_memory_exit_gives_a_helpful_error(manager, make_spec) -> None:
    spec = make_spec(env={"FAKE_LLAMA_DIE_WITH": "CUDA error: out of memory"})
    with pytest.raises(ServerStartError) as err:
        manager.ensure(spec, timeout=30)
    assert "did not fit in memory" in str(err.value)
    assert "ctx" in str(err.value)
    assert manager.info() is None


def test_other_early_exit_points_to_the_log(manager, make_spec) -> None:
    spec = make_spec(env={"FAKE_LLAMA_DIE_WITH": "error: unknown model architecture"})
    with pytest.raises(ServerStartError) as err:
        manager.ensure(spec, timeout=30)
    assert "unknown model architecture" in str(err.value)
    assert "qed doctor" in str(err.value)


def test_missing_model_file_is_a_plain_error(manager, make_spec, tmp_path: Path) -> None:
    with pytest.raises(ServerStartError) as err:
        manager.ensure(make_spec(model_path=tmp_path / "nope.gguf"))
    assert "qed models download" in str(err.value)


def test_idle_unload(make_spec) -> None:
    now = [10_000.0]
    mgr = ServerManager(clock=lambda: now[0])
    try:
        mgr.ensure(make_spec())
        assert mgr.unload_if_idle(10) is False  # just used
        now[0] += 9 * 60
        assert mgr.unload_if_idle(10) is False  # 9 minutes: still within keep-alive
        mgr.touch()
        now[0] += 10 * 60 + 1
        assert mgr.idle_seconds() > 600
        assert mgr.unload_if_idle(10) is True
        assert mgr.info() is None
    finally:
        mgr.stop()


def test_idle_watcher_stops_an_idle_server(make_spec) -> None:
    now = [5_000.0]
    mgr = ServerManager(clock=lambda: now[0])
    try:
        mgr.ensure(make_spec())
        now[0] += 3600
        mgr.start_idle_watcher(keep_alive_minutes=10, interval=0.05)
        deadline = time.monotonic() + 10
        while mgr.info() is not None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert mgr.info() is None
    finally:
        mgr.stop_idle_watcher()
        mgr.stop()


def test_release_keeps_the_server_while_another_client_is_alive(manager, make_spec) -> None:
    manager.ensure(make_spec())
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        manager.clients_dir.mkdir(parents=True, exist_ok=True)
        (manager.clients_dir / f"{other.pid}.json").write_text("{}", encoding="utf-8")
        assert manager.live_clients() == sorted([os.getpid(), other.pid])
        assert manager.release(unload_on_exit=True) is False  # someone else still uses it
        assert manager.info() is not None
    finally:
        other.kill()
        other.wait()
    # the other client is gone now: the last one out stops the server
    assert manager.live_clients() == []
    assert manager.release(unload_on_exit=True) is True
    assert manager.info() is None


def test_release_without_unload_on_exit_leaves_the_server(manager, make_spec) -> None:
    manager.ensure(make_spec())
    assert manager.release(unload_on_exit=False) is False
    assert manager.info() is not None


# ------------------------------------------------------------------ the client


def test_llamacpp_client_chats_through_the_shared_server(manager, make_spec, tmp_path) -> None:
    spec = make_spec()
    client = LlamaCppClient(spec, manager)
    assert client.name == "llamacpp:tiny"
    tokens: list[str] = []
    reply = client.chat(
        [{"role": "user", "content": "hello world"}],
        temperature=0.2,
        max_tokens=50,
        on_token=tokens.append,
    )
    assert reply == "fake reply to: hello world"
    assert "".join(tokens) == reply
    assert client.health() is True
    assert client.context_tokens == 4096
    request = json.loads(Path(spec.env["FAKE_LLAMA_REQUEST_FILE"]).read_text(encoding="utf-8"))
    assert request["chat_template_kwargs"] == {"enable_thinking": False}
    assert request["temperature"] == 0.2 and request["max_tokens"] == 50
    # a second client reuses the same server instead of starting another
    other = LlamaCppClient(spec, ServerManager())
    other.chat([{"role": "user", "content": "x"}], temperature=0, max_tokens=5)
    assert len(fake_children()) == 1
    client.unload()
    assert manager.info() is None
    assert client.health() is False


def test_llamacpp_client_restarts_a_server_that_went_away(manager, make_spec) -> None:
    client = LlamaCppClient(make_spec(), manager)
    client.chat([{"role": "user", "content": "a"}], temperature=0, max_tokens=5)
    first = client.info
    manager.stop()
    reply = client.chat([{"role": "user", "content": "b"}], temperature=0, max_tokens=5)
    assert reply == "fake reply to: b"
    assert client.info.pid != first.pid


def test_resolve_model_path(tmp_path: Path, monkeypatch) -> None:
    gguf = tmp_path / "mine.gguf"
    gguf.write_bytes(b"x")
    assert resolve_model_path(str(gguf)) == ("mine", gguf)
    with pytest.raises(ModelError) as err:
        resolve_model_path(str(tmp_path / "missing.gguf"))
    assert "qed setup" in str(err.value)
    with pytest.raises(ModelError) as err2:
        resolve_model_path("qwen3.5-4b-q4km")  # in the catalog but not downloaded
    assert "qed models download qwen3.5-4b-q4km" in str(err2.value)
    with pytest.raises(ModelError):
        resolve_model_path("not-in-catalog")


def test_a_new_context_size_restarts_the_server(manager, make_spec) -> None:
    """A server started with a smaller context must not be reused: long prompts would fail."""
    first = manager.ensure(make_spec(ctx=4096))
    second = manager.ensure(make_spec(ctx=8192))
    assert second.pid != first.pid and second.ctx == 8192
    deadline = time.monotonic() + 5
    while psutil.pid_exists(first.pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert len(fake_children()) == 1


def test_a_long_answer_keeps_the_server_marked_as_in_use(manager, make_spec, monkeypatch) -> None:
    """Another client's idle watcher must not stop the server while an answer is streaming."""
    import qedcode.runtime.llamacpp as llamacpp

    client = LlamaCppClient(make_spec(), manager)
    client.chat([{"role": "user", "content": "warm up"}], temperature=0, max_tokens=5)
    touches: list[float] = []
    real_touch = manager.touch
    monkeypatch.setattr(manager, "touch", lambda: (touches.append(1), real_touch())[1])
    monkeypatch.setattr(llamacpp, "TOUCH_EVERY_S", 0.0)
    client.chat([{"role": "user", "content": "a b c d e"}], temperature=0, max_tokens=50)
    assert len(touches) >= 3  # before, during the stream and after
