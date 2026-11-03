"""LLM gateway: an OpenAI-compatible front door with auth, guard rails and metrics."""

from __future__ import annotations

import hmac
import logging
import time
import uuid
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from app import metrics
from app.backends import Backend, BackendError, build_backend
from app.config import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("llmops-gateway")


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[Message] = Field(min_length=1)
    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


def create_app(settings: Settings | None = None, backend: Backend | None = None) -> FastAPI:
    settings = settings or get_settings()
    backend = backend or build_backend(settings)
    app = FastAPI(title="llmops-gateway", version="0.1.0")

    def require_api_key(authorization: str | None = Header(default=None)) -> None:
        if not settings.gateway_api_keys:
            return  # auth disabled (local development)
        token = (authorization or "").removeprefix("Bearer ").strip()
        # Constant-time comparison against every configured key.
        if not any(hmac.compare_digest(token, k) for k in settings.gateway_api_keys):
            raise HTTPException(status_code=401, detail="invalid or missing API key")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        ok = await backend.ready()
        return JSONResponse({"ready": ok, "backend": backend.name}, status_code=200 if ok else 503)

    @app.get("/metrics")
    async def prometheus_metrics() -> PlainTextResponse:
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/chat/completions", dependencies=[Depends(require_api_key)])
    async def chat_completions(req: ChatRequest, request: Request) -> dict:
        model = req.model or settings.default_model
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())

        prompt_chars = sum(len(m.content) for m in req.messages)
        if prompt_chars > settings.max_prompt_chars:
            metrics.REQUESTS.labels(model, backend.name, "rejected").inc()
            raise HTTPException(status_code=413, detail="prompt too large")
        max_tokens = min(req.max_tokens or settings.max_output_tokens, settings.max_output_tokens)

        start = time.perf_counter()
        metrics.INFLIGHT.inc()
        try:
            text, p_tok, c_tok = await backend.complete(
                model, [m.model_dump() for m in req.messages], max_tokens, req.temperature
            )
        except BackendError as exc:
            metrics.REQUESTS.labels(model, backend.name, "error").inc()
            # Prompts are never logged; only metadata.
            log.warning("request_id=%s model=%s upstream_error=%s", request_id, model, exc)
            raise HTTPException(status_code=502, detail="upstream model error") from exc
        finally:
            metrics.INFLIGHT.dec()
            metrics.LATENCY.labels(model, backend.name).observe(time.perf_counter() - start)

        metrics.REQUESTS.labels(model, backend.name, "ok").inc()
        metrics.TOKENS.labels(model, "prompt").inc(p_tok)
        metrics.TOKENS.labels(model, "completion").inc(c_tok)
        cost = (
            p_tok / 1000 * settings.price_per_1k_prompt
            + c_tok / 1000 * settings.price_per_1k_completion
        )
        if cost:
            metrics.COST.labels(model).inc(cost)
        log.info(
            "request_id=%s model=%s prompt_tokens=%d completion_tokens=%d",
            request_id,
            model,
            p_tok,
            c_tok,
        )

        return {
            "id": f"chatcmpl-{request_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": p_tok,
                "completion_tokens": c_tok,
                "total_tokens": p_tok + c_tok,
            },
        }

    return app


app = create_app()
