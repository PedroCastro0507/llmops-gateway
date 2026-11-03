"""Model backends. Each returns (text, prompt_tokens, completion_tokens)."""

from __future__ import annotations

from typing import Protocol

import httpx

from app.config import Settings


class BackendError(Exception):
    """Upstream model failed or returned something unusable."""


def estimate_tokens(text: str) -> int:
    # Rough rule of thumb (~4 characters per token) used only when the
    # upstream does not report usage.
    return max(1, len(text) // 4) if text else 0


class Backend(Protocol):
    name: str

    async def complete(
        self, model: str, messages: list[dict], max_tokens: int, temperature: float
    ) -> tuple[str, int, int]: ...

    async def ready(self) -> bool: ...


class MockBackend:
    """Deterministic backend: no network, no secrets. Used in CI and demos."""

    name = "mock"

    async def complete(self, model, messages, max_tokens, temperature):
        last = messages[-1]["content"] if messages else ""
        text = f"[mock:{model}] You said: {last}"
        words = text.split()[:max_tokens]
        text = " ".join(words)
        prompt_tokens = sum(estimate_tokens(m["content"]) for m in messages)
        return text, prompt_tokens, estimate_tokens(text)

    async def ready(self) -> bool:
        return True


class OpenAICompatibleBackend:
    """Any server exposing /v1/chat/completions (Ollama, vLLM, OpenAI, ...)."""

    name = "openai"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        headers = {}
        if settings.upstream_api_key:
            headers["Authorization"] = f"Bearer {settings.upstream_api_key}"
        self._client = client or httpx.AsyncClient(
            base_url=settings.upstream_base_url.rstrip("/"),
            headers=headers,
            timeout=settings.request_timeout_s,
        )

    async def complete(self, model, messages, max_tokens, temperature):
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        try:
            resp = await self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise BackendError(f"upstream unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise BackendError(f"upstream returned HTTP {resp.status_code}")
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise BackendError("upstream response not in OpenAI format") from exc
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens") or sum(
            estimate_tokens(m["content"]) for m in messages
        )
        completion_tokens = usage.get("completion_tokens") or estimate_tokens(text)
        return text, int(prompt_tokens), int(completion_tokens)

    async def ready(self) -> bool:
        try:
            resp = await self._client.get("/models", timeout=2.0)
            return resp.status_code < 500
        except httpx.HTTPError:
            return False


def build_backend(settings: Settings) -> Backend:
    if settings.backend == "mock":
        return MockBackend()
    if settings.backend == "openai":
        return OpenAICompatibleBackend(settings)
    raise ValueError(f"unknown BACKEND={settings.backend!r} (use 'mock' or 'openai')")
