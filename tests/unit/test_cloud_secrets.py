from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import KeyringError

from qedcode.config import GlobalConfig, qedcode_home, save_global
from qedcode.logging_setup import setup_logging
from qedcode.models.schemas import Mode
from qedcode.runtime import cloud as cloud_module
from qedcode.runtime.client import ModelClient, ModelError, make_client
from qedcode.runtime.cloud import CloudClient
from qedcode.runtime.secrets import (
    MaskingFilter,
    SecretsError,
    delete_api_key,
    env_var_for,
    get_api_key,
    mask_secrets,
    needs_key,
    provider_of,
    set_api_key,
)

TEST_KEY = "sk-test-SECRET-1234567890abcdef"
MSGS = [{"role": "user", "content": "hi"}]


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))

    def delete_password(self, service, username):
        if (service, username) not in self.store:
            from keyring.errors import PasswordDeleteError

            raise PasswordDeleteError("not stored")
        del self.store[(service, username)]


class BrokenKeyring(KeyringBackend):
    priority = 1

    def set_password(self, service, username, password):
        raise KeyringError("no backend")

    def get_password(self, service, username):
        raise KeyringError("no backend")

    def delete_password(self, service, username):
        raise KeyringError("no backend")


@pytest.fixture
def memory_keyring():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def _no_provider_env(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "FOO_API_KEY"):
        monkeypatch.delenv(var, raising=False)


# ------------------------------------------------------------------ providers and keys


def test_provider_and_env_var_names() -> None:
    assert provider_of("openrouter/anthropic/claude-sonnet-4") == "openrouter"
    assert provider_of("anthropic/claude-sonnet-4") == "anthropic"
    assert provider_of("gpt-4o") == "openai"
    assert provider_of("claude-sonnet-4") == "anthropic"
    assert provider_of("gemini-2.5-pro") == "gemini"
    assert provider_of("ollama/llama3") == "ollama"
    assert env_var_for("openai") == "OPENAI_API_KEY"
    assert env_var_for("my-vendor") == "MY_VENDOR_API_KEY"
    assert needs_key("openai") and not needs_key("ollama") and not needs_key("lm_studio")


def test_keys_are_stored_in_the_keychain_and_read_back(memory_keyring) -> None:
    assert get_api_key("openai") is None
    set_api_key("OpenAI", f"  {TEST_KEY}  ")
    assert memory_keyring.store == {("qedcode", "openai"): TEST_KEY}
    assert get_api_key("openai") == TEST_KEY
    assert delete_api_key("openai") is True
    assert delete_api_key("openai") is False
    assert get_api_key("openai") is None


def test_env_var_is_the_fallback_and_the_keychain_wins(memory_keyring, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "from-env-123456")
    assert get_api_key("openai") == "from-env-123456"
    set_api_key("openai", "from-keychain-123456")
    assert get_api_key("openai") == "from-keychain-123456"


def test_a_missing_or_broken_keychain_never_crashes_reading() -> None:
    previous = keyring.get_keyring()
    keyring.set_keyring(BrokenKeyring())
    try:
        assert get_api_key("openai") is None
        with pytest.raises(SecretsError) as err:
            set_api_key("openai", TEST_KEY)
        assert "OPENAI_API_KEY" in str(err.value)
        assert TEST_KEY not in str(err.value)
        assert delete_api_key("openai") is False
    finally:
        keyring.set_keyring(previous)


def test_empty_keys_are_rejected(memory_keyring) -> None:
    with pytest.raises(SecretsError):
        set_api_key("openai", "   ")


def test_mask_secrets() -> None:
    text = f"error calling with {TEST_KEY} and Bearer abcdef123456789 and api_key=hunter2hunter2"
    masked = mask_secrets(text, [TEST_KEY])
    assert TEST_KEY not in masked
    assert "abcdef123456789" not in masked
    assert "hunter2hunter2" not in masked
    assert masked.count("***") >= 3
    assert mask_secrets("nothing secret here") == "nothing secret here"


# ------------------------------------------------------------------ CloudClient with a mocked litellm


def chunk(text: str | None = None, usage=None):
    choices = [SimpleNamespace(delta=SimpleNamespace(content=text))] if text is not None else []
    return SimpleNamespace(choices=choices, usage=usage)


class FakeLitellm:
    def __init__(self, chunks=None, error: Exception | None = None) -> None:
        self.chunks, self.error, self.calls = chunks or [], error, []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return iter(self.chunks)

    def token_counter(self, model, messages):
        return 7


@pytest.fixture
def fake_litellm(monkeypatch):
    def install(**kw) -> FakeLitellm:
        fake = FakeLitellm(**kw)
        monkeypatch.setattr(cloud_module, "_load_litellm", lambda: fake)
        return fake

    return install


def test_cloud_chat_streams_passes_the_key_and_counts_tokens(memory_keyring, fake_litellm) -> None:
    set_api_key("openrouter", TEST_KEY)
    usage = SimpleNamespace(prompt_tokens=12, completion_tokens=5)
    fake = fake_litellm(chunks=[chunk("Hel"), chunk("lo"), chunk(None, usage=usage)])
    client = CloudClient("openrouter/anthropic/claude-sonnet-4")
    assert isinstance(client, ModelClient)
    tokens: list[str] = []
    reply = client.chat(
        MSGS, temperature=0.3, max_tokens=99, stop=["X"], on_token=tokens.append, tag="coder"
    )
    assert reply == "Hello" and tokens == ["Hel", "lo"]
    call = fake.calls[0]
    assert call["model"] == "openrouter/anthropic/claude-sonnet-4"
    assert call["api_key"] == TEST_KEY
    assert call["stream"] is True and call["stream_options"] == {"include_usage": True}
    assert call["temperature"] == 0.3 and call["max_tokens"] == 99 and call["stop"] == ["X"]
    assert client.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
        "calls": 1,
    }
    assert client.name == "cloud:openrouter/anthropic/claude-sonnet-4"
    assert client.is_remote is True


def test_usage_is_estimated_when_the_provider_sends_none(memory_keyring, fake_litellm) -> None:
    set_api_key("openai", TEST_KEY)
    fake_litellm(chunks=[chunk("abcdefg")])
    client = CloudClient("gpt-4o")
    client.chat(MSGS, temperature=0, max_tokens=5)
    assert client.usage["prompt_tokens"] == 7  # from token_counter
    assert client.usage["completion_tokens"] == 3  # 7 chars / 3.5 + 1
    assert client.usage["calls"] == 1


def test_no_key_gives_a_helpful_error_and_makes_no_call(memory_keyring, fake_litellm) -> None:
    fake = fake_litellm(chunks=[chunk("x")])
    client = CloudClient("anthropic/claude-sonnet-4")
    assert client.health() is False
    with pytest.raises(ModelError) as err:
        client.chat(MSGS, temperature=0, max_tokens=5)
    assert "ANTHROPIC_API_KEY" in str(err.value) and "qed setup" in str(err.value)
    assert fake.calls == []


def test_local_providers_need_no_key(memory_keyring, fake_litellm) -> None:
    fake = fake_litellm(chunks=[chunk("ok")])
    client = CloudClient("ollama/llama3")
    assert client.health() is True
    assert client.chat(MSGS, temperature=0, max_tokens=5) == "ok"
    assert "api_key" not in fake.calls[0]


class AuthenticationError(Exception):
    pass


class RateLimitError(Exception):
    pass


class APIConnectionError(Exception):
    pass


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthenticationError("401 bad key"), "rejected the API key"),
        (RateLimitError("429"), "too many requests"),
        (APIConnectionError("dns"), "internet connection"),
        (RuntimeError("weird"), "could not answer"),
    ],
)
def test_provider_errors_become_plain_english(
    memory_keyring, fake_litellm, error, expected
) -> None:
    set_api_key("openai", TEST_KEY)
    fake_litellm(error=error)
    with pytest.raises(ModelError) as err:
        CloudClient("gpt-4o").chat(MSGS, temperature=0, max_tokens=5)
    assert expected in str(err.value)


def test_the_key_never_appears_in_errors_or_logs(memory_keyring, fake_litellm, caplog) -> None:
    set_api_key("openai", TEST_KEY)
    fake_litellm(error=AuthenticationError(f"Incorrect API key provided: {TEST_KEY}"))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ModelError) as err:
            CloudClient("gpt-4o").chat(MSGS, temperature=0, max_tokens=5)
    assert TEST_KEY not in str(err.value)
    assert TEST_KEY not in caplog.text
    assert "***" in str(err.value)


def test_an_on_token_exception_passes_through_unchanged(memory_keyring, fake_litellm) -> None:
    class Cancelled(Exception):
        pass

    def stop(_piece: str) -> None:
        raise Cancelled

    set_api_key("openai", TEST_KEY)
    fake_litellm(chunks=[chunk("a"), chunk("b")])
    with pytest.raises(Cancelled):
        CloudClient("gpt-4o").chat(MSGS, temperature=0, max_tokens=5, on_token=stop)


def test_make_client_builds_cloud_main_and_helper(memory_keyring) -> None:
    cfg = GlobalConfig()
    cfg.engine.mode = Mode.CLOUD
    with pytest.raises(ModelError) as err:
        make_client(cfg)
    assert "No cloud model" in str(err.value)
    cfg.cloud.model = "openrouter/anthropic/claude-sonnet-4"
    client = make_client(cfg)
    assert isinstance(client, CloudClient) and client.model == cfg.cloud.model
    assert client.context_tokens >= 32768
    cfg.engine.mode = Mode.MIX
    cfg.helper.model = "gpt-4o"
    cfg.helper.backend = "cloud"
    helper = make_client(cfg, role="helper")
    assert isinstance(helper, CloudClient) and helper.model == "gpt-4o"


# ------------------------------------------------------------------ nothing leaks to disk


def test_searching_config_logs_and_the_repo_for_a_test_key_finds_nothing(
    memory_keyring, fake_litellm, tmp_path: Path, caplog
) -> None:
    set_api_key("openai", TEST_KEY)
    cfg = GlobalConfig()
    cfg.cloud.model = "gpt-4o"
    save_global(cfg)
    log_path = setup_logging()
    fake_litellm(error=AuthenticationError(f"bad key {TEST_KEY}"))
    with pytest.raises(ModelError):
        CloudClient("gpt-4o").chat(MSGS, temperature=0, max_tokens=5)
    logging.getLogger("qedcode.test").warning("leaky line with %s", TEST_KEY)
    for handler in logging.getLogger("qedcode").handlers:
        handler.flush()
    assert log_path is not None
    leaked = [
        str(p)
        for p in qedcode_home().rglob("*")
        if p.is_file() and TEST_KEY in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert leaked == []
    assert "leaky line" in log_path.read_text(encoding="utf-8")  # the line was logged, masked
    repo = Path(__file__).resolve().parents[2]
    for path in list((repo / "src").rglob("*.py")) + [repo / "pyproject.toml"]:
        assert TEST_KEY not in path.read_text(encoding="utf-8")
    for handler in list(logging.getLogger("qedcode").handlers):
        if getattr(handler, "_qed_file", False):
            logging.getLogger("qedcode").removeHandler(handler)
            handler.close()


def test_masking_filter_masks_log_arguments(caplog) -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "key is %s", (TEST_KEY,), None)
    MaskingFilter().filter(record)
    assert TEST_KEY not in record.getMessage()
