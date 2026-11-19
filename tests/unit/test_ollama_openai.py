from __future__ import annotations

import json

import httpx
import pytest

from qedcode.config import GlobalConfig
from qedcode.models.schemas import Mode
from qedcode.runtime.client import (
    ModelClient,
    ModelError,
    ServerNotRunning,
    make_client,
    ollama_base_url,
)
from qedcode.runtime.ollama import OllamaClient
from qedcode.runtime.openai_compat import OpenAICompatClient, parse_sse_data

MSGS = [{"role": "user", "content": "hi"}]


def ndjson(*events: dict) -> bytes:
    return ("\n".join(json.dumps(e) for e in events) + "\n").encode()


def sse(*chunks: str, done: bool = True) -> bytes:
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n" for c in chunks]
    if done:
        lines.append("data: [DONE]\n")
    return "\n".join(lines).encode()


# ------------------------------------------------------------------ Ollama


def test_ollama_chat_streams_and_sends_the_right_options() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "Hel"}, "done": False},
                {"message": {"content": "lo"}, "done": False},
                {"message": {"content": ""}, "done": True},
            ),
        )

    client = OllamaClient(
        "qwen3.5:4b", ctx=8192, keep_alive_minutes=7, transport=httpx.MockTransport(handler)
    )
    tokens: list[str] = []
    reply = client.chat(
        MSGS, temperature=0.7, max_tokens=123, stop=["END"], on_token=tokens.append, tag="coder"
    )
    assert reply == "Hello"
    assert tokens == ["Hel", "lo"]
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    body = seen["body"]
    assert body["model"] == "qwen3.5:4b"
    assert body["stream"] is True
    assert body["messages"] == MSGS
    assert body["keep_alive"] == "7m"
    assert body["options"] == {
        "num_ctx": 8192,
        "temperature": 0.7,
        "num_predict": 123,
        "stop": ["END"],
    }
    assert isinstance(client, ModelClient)
    assert client.name == "ollama:qwen3.5:4b"
    assert client.context_tokens == 8192


def test_ollama_error_line_becomes_a_helpful_error() -> None:
    def handler(request):
        return httpx.Response(200, content=ndjson({"error": "model 'x' not found"}))

    client = OllamaClient("x", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError) as err:
        client.chat(MSGS, temperature=0, max_tokens=5)
    assert "ollama pull x" in str(err.value)


def test_ollama_http_error_and_no_server() -> None:
    client = OllamaClient(
        "x", transport=httpx.MockTransport(lambda r: httpx.Response(404, json={"error": "nope"}))
    )
    with pytest.raises(ModelError) as err:
        client.chat(MSGS, temperature=0, max_tokens=5)
    assert "HTTP 404" in str(err.value)
    assert "ollama pull x" in str(err.value)

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    down = OllamaClient("x", transport=httpx.MockTransport(refuse))
    with pytest.raises(ServerNotRunning) as err2:
        down.chat(MSGS, temperature=0, max_tokens=5)
    assert "ollama serve" in str(err2.value)
    assert down.is_running() is False
    assert down.health() is False
    with pytest.raises(ServerNotRunning):
        down.list_models()
    with pytest.raises(ServerNotRunning):
        down.pull("x")


def test_ollama_read_timeout_message() -> None:
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    client = OllamaClient("x", transport=httpx.MockTransport(slow))
    with pytest.raises(ModelError) as err:
        client.chat(MSGS, temperature=0, max_tokens=5)
    assert "600 seconds" in str(err.value)


def test_ollama_list_models_and_is_running() -> None:
    def handler(request):
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "a:1b"}, {"name": "b:2b"}, {}]})

    client = OllamaClient("a:1b", transport=httpx.MockTransport(handler))
    assert client.list_models() == ["a:1b", "b:2b"]
    assert client.is_running() is True
    assert client.health() is True


def test_ollama_pull_reports_progress() -> None:
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=ndjson(
                {"status": "pulling manifest"},
                {"status": "downloading", "total": 100, "completed": 40},
                {"status": "downloading", "total": 100, "completed": 100},
                {"status": "success"},
            ),
        )

    client = OllamaClient("m", transport=httpx.MockTransport(handler))
    updates: list[tuple[str, int, int]] = []
    client.pull("qwen3.5:4b", lambda s, c, t: updates.append((s, c, t)))
    assert seen["body"] == {"model": "qwen3.5:4b", "stream": True}
    assert updates[0] == ("pulling manifest", 0, 0)
    assert ("downloading", 40, 100) in updates
    assert updates[-1][0] == "success"


def test_ollama_unload_sends_keep_alive_zero_and_never_raises() -> None:
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    OllamaClient("m1", transport=httpx.MockTransport(handler)).unload()
    assert seen["body"]["keep_alive"] == 0
    assert seen["body"]["model"] == "m1"

    def refuse(request):
        raise httpx.ConnectError("x", request=request)

    OllamaClient("m1", transport=httpx.MockTransport(refuse)).unload()  # no exception


def test_an_on_token_exception_aborts_the_reply() -> None:
    class Stop(Exception):
        pass

    def stop_now(piece: str) -> None:
        raise Stop

    client = OllamaClient(
        "m",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=ndjson({"message": {"content": "a"}}))
        ),
    )
    with pytest.raises(Stop):
        client.chat(MSGS, temperature=0, max_tokens=5, on_token=stop_now)


# ------------------------------------------------------------------ OpenAI compatible


def test_openai_compat_streams_sse_and_sends_the_payload() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=sse("Hel", "lo"))

    client = OpenAICompatClient(
        "my-model",
        "http://127.0.0.1:1234/v1/",
        ctx=4096,
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )
    tokens: list[str] = []
    assert (
        client.chat(MSGS, temperature=0.3, max_tokens=50, stop=["X"], on_token=tokens.append)
        == "Hello"
    )
    assert tokens == ["Hel", "lo"]
    assert seen["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "my-model"
    assert seen["body"]["stream"] is True
    assert seen["body"]["temperature"] == 0.3
    assert seen["body"]["max_tokens"] == 50
    assert seen["body"]["stop"] == ["X"]
    assert client.name == "openai_compat:my-model"
    assert client.context_tokens == 4096


def test_openai_compat_no_key_means_no_auth_header() -> None:
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=sse("ok"))

    OpenAICompatClient("m", "http://x/v1", transport=httpx.MockTransport(handler)).chat(
        MSGS, temperature=0, max_tokens=5
    )
    assert seen["auth"] is None


def test_openai_compat_ignores_empty_and_odd_chunks() -> None:
    body = (
        b": keep-alive comment\n\n"
        b'data: {"choices": []}\n\n'
        b'data: {"choices": [{"delta": {"role": "assistant"}}]}\n\n'
        b"data: not json\n\n" + sse("A", "B")
    )
    client = OpenAICompatClient(
        "m",
        "http://x/v1",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body)),
    )
    assert client.chat(MSGS, temperature=0, max_tokens=5) == "AB"


def test_openai_compat_errors_are_plain_english() -> None:
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    down = OpenAICompatClient(
        "m", "http://127.0.0.1:1234/v1", transport=httpx.MockTransport(refuse)
    )
    with pytest.raises(ServerNotRunning) as err:
        down.chat(MSGS, temperature=0, max_tokens=5)
    assert "127.0.0.1:1234" in str(err.value)
    assert "Start the server" in str(err.value)
    assert down.health() is False

    unauthorized = OpenAICompatClient(
        "m",
        "http://x/v1",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})
        ),
    )
    with pytest.raises(ModelError) as err2:
        unauthorized.chat(MSGS, temperature=0, max_tokens=5)
    assert "HTTP 401" in str(err2.value)
    assert "bad key" in str(err2.value)
    assert "API key" in str(err2.value)

    in_stream = OpenAICompatClient(
        "m",
        "http://x/v1",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=b'data: {"error": {"message": "oom"}}\n\n')
        ),
    )
    with pytest.raises(ModelError) as err3:
        in_stream.chat(MSGS, temperature=0, max_tokens=5)
    assert "oom" in str(err3.value)


def test_openai_compat_health_uses_models_endpoint() -> None:
    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": []})

    assert OpenAICompatClient("m", "http://x/v1", transport=httpx.MockTransport(handler)).health()
    bad = OpenAICompatClient(
        "m", "http://x/v1", transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    assert bad.health() is False
    bad.unload()  # a no-op, must not raise


def test_parse_sse_data() -> None:
    assert parse_sse_data("data: hello") == "hello"
    assert parse_sse_data("data:{}") == "{}"
    assert parse_sse_data("") is None
    assert parse_sse_data(": comment") is None
    assert parse_sse_data("event: ping") is None


# ------------------------------------------------------------------ factory


def test_ollama_base_url_from_env(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert ollama_base_url() == "http://127.0.0.1:11434"
    monkeypatch.setenv("OLLAMA_HOST", "10.0.0.5")
    assert ollama_base_url() == "http://10.0.0.5:11434"
    monkeypatch.setenv("OLLAMA_HOST", "10.0.0.5:9999")
    assert ollama_base_url() == "http://10.0.0.5:9999"
    monkeypatch.setenv("OLLAMA_HOST", "http://box:1234/")
    assert ollama_base_url() == "http://box:1234"


def test_make_client_builds_local_backends(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    cfg = GlobalConfig()
    cfg.local.backend = "ollama"
    cfg.local.model = "qwen3.5:4b"
    cfg.local.ctx = 8192
    client = make_client(cfg)
    assert isinstance(client, OllamaClient)
    assert client.model == "qwen3.5:4b"
    assert client.context_tokens == 8192
    assert client.keep_alive == "10m"

    cfg.local.backend = "openai_compat"
    cfg.local.base_url = "http://127.0.0.1:1234/v1"
    cfg.local.model = "lm-model"
    compat = make_client(cfg)
    assert isinstance(compat, OpenAICompatClient)
    assert compat.base_url == "http://127.0.0.1:1234/v1"


def test_make_client_openai_compat_needs_a_base_url() -> None:
    cfg = GlobalConfig()
    cfg.local.backend = "openai_compat"
    with pytest.raises(ModelError) as err:
        make_client(cfg)
    assert "base_url" in str(err.value)
    assert "qed setup" in str(err.value)


def test_make_client_helper_backends() -> None:
    cfg = GlobalConfig()
    cfg.engine.mode = Mode.MIX
    with pytest.raises(ModelError) as err:
        make_client(cfg, role="helper")
    assert "No helper model" in str(err.value)

    cfg.helper.model = "llama3.1:8b"
    cfg.helper.backend = "ollama"
    helper = make_client(cfg, role="helper")
    assert isinstance(helper, OllamaClient) and helper.model == "llama3.1:8b"

    cfg.helper.backend = "openai_compat"
    with pytest.raises(ModelError):
        make_client(cfg, role="helper")  # no base_url
    cfg.helper.base_url = "http://127.0.0.1:1234/v1"
    assert isinstance(make_client(cfg, role="helper"), OpenAICompatClient)
