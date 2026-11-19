"""API keys: OS keychain (keyring service "qedcode") or the provider's usual environment variable.

Keys are never written to config files, logs, receipts or prompts. Every error that might carry a
key goes through `mask_secrets` first.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable

import keyring
from keyring.errors import KeyringError

SERVICE = "qedcode"
MASK = "***"

# provider (the part of a LiteLLM model string before the first "/") -> its usual env var
ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "google": "GEMINI_API_KEY",
    "vertex_ai": "GOOGLE_APPLICATION_CREDENTIALS",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "together_ai": "TOGETHERAI_API_KEY",
    "xai": "XAI_API_KEY",
    "cohere": "COHERE_API_KEY",
    "fireworks_ai": "FIREWORKS_API_KEY",
    "perplexity": "PERPLEXITYAI_API_KEY",
}
NO_KEY_PROVIDERS = {"ollama", "ollama_chat", "lm_studio", "hosted_vllm", "llamafile", "vllm"}
_BARE_PREFIXES = (
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("claude", "anthropic"),
    ("gemini", "gemini"),
)
_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)(\s*[=:]\s*)[^\s,;'\"]{6,}"),
)


class SecretsError(RuntimeError):
    """Storing or reading a key failed. The message says what to do next."""


def provider_of(model: str) -> str:
    """Provider of a LiteLLM model string: 'openrouter/x/y' -> openrouter, 'gpt-4o' -> openai."""
    head = model.split("/", 1)[0].lower()
    if "/" in model:
        return head
    for prefix, provider in _BARE_PREFIXES:
        if head.startswith(prefix):
            return provider
    return head


def env_var_for(provider: str) -> str:
    """The environment variable that conventionally holds this provider's key."""
    default = re.sub(r"[^A-Za-z0-9]", "_", provider).upper() + "_API_KEY"
    return ENV_VARS.get(provider.lower(), default)


def needs_key(provider: str) -> bool:
    return provider.lower() not in NO_KEY_PROVIDERS


def get_api_key(provider: str) -> str | None:
    """The saved key (keychain first), else the provider's environment variable, else None."""
    provider = provider.lower()
    try:
        stored = keyring.get_password(SERVICE, provider)
    except Exception:  # noqa: BLE001 - no keychain on this machine is not an error here
        stored = None
    if stored:
        return stored
    value = os.environ.get(env_var_for(provider))
    return value or None


def set_api_key(provider: str, key: str) -> None:
    """Save a key in the OS keychain only (never in a file)."""
    key = key.strip()
    if not key:
        raise SecretsError("The API key is empty, so nothing was saved. Paste the key and retry.")
    try:
        keyring.set_password(SERVICE, provider.lower(), key)
    except KeyringError as exc:
        raise SecretsError(
            f"QEDCode could not save the key in your system keychain ({type(exc).__name__}). "
            f"Set the {env_var_for(provider)} environment variable instead; QEDCode never stores "
            "keys in files."
        ) from exc


def delete_api_key(provider: str) -> bool:
    """Remove a saved key. Returns False when none was stored."""
    try:
        keyring.delete_password(SERVICE, provider.lower())
        return True
    except KeyringError:
        return False


def mask_secrets(text: str, extra: Iterable[str | None] = ()) -> str:
    """Replace known keys (and anything shaped like one) with ***."""
    for secret in extra:
        if secret and len(secret) >= 4:
            text = text.replace(secret, MASK)
    for pattern in _PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
        else:
            text = pattern.sub(MASK, text)
    return text


class MaskingFilter(logging.Filter):
    """A log filter that masks key-like text in every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = mask_secrets(record.getMessage())
            record.args = ()
        except Exception:  # noqa: BLE001 - logging must never fail because of masking
            pass
        return True
