"""Runtime configuration, read once from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # "mock" needs nothing and is used by CI; "openai" talks to any
    # OpenAI-compatible API (Ollama, vLLM, OpenAI, ...).
    backend: str = field(default_factory=lambda: os.getenv("BACKEND", "mock"))
    upstream_base_url: str = field(
        default_factory=lambda: os.getenv("UPSTREAM_BASE_URL", "http://ollama:11434/v1")
    )
    upstream_api_key: str = field(default_factory=lambda: os.getenv("UPSTREAM_API_KEY", ""))
    default_model: str = field(default_factory=lambda: os.getenv("DEFAULT_MODEL", "llama3.2:1b"))
    request_timeout_s: float = field(default_factory=lambda: _float("REQUEST_TIMEOUT_S", 60.0))
    # Guard rails
    max_prompt_chars: int = field(default_factory=lambda: _int("MAX_PROMPT_CHARS", 16000))
    max_output_tokens: int = field(default_factory=lambda: _int("MAX_OUTPUT_TOKENS", 1024))
    # Comma-separated keys clients must send as "Authorization: Bearer <key>".
    # Empty = auth disabled (local development only).
    gateway_api_keys: frozenset[str] = field(
        default_factory=lambda: frozenset(
            k.strip() for k in os.getenv("GATEWAY_API_KEYS", "").split(",") if k.strip()
        )
    )
    # Cost estimate in USD per 1K tokens (0 for self-hosted models).
    price_per_1k_prompt: float = field(
        default_factory=lambda: _float("PRICE_PER_1K_PROMPT_USD", 0.0)
    )
    price_per_1k_completion: float = field(
        default_factory=lambda: _float("PRICE_PER_1K_COMPLETION_USD", 0.0)
    )


def get_settings() -> Settings:
    return Settings()
