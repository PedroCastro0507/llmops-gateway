import httpx
import pytest
from fastapi.testclient import TestClient

from app.backends import BackendError, MockBackend, OpenAICompatibleBackend, estimate_tokens
from app.config import Settings
from app.main import create_app


def make_client(backend=None, **overrides) -> TestClient:
    settings = Settings(**overrides)
    return TestClient(create_app(settings, backend or MockBackend()))


def chat(client, content="hello", headers=None, **body):
    payload = {"messages": [{"role": "user", "content": content}], **body}
    return client.post("/v1/chat/completions", json=payload, headers=headers or {})


def test_health_and_ready():
    client = make_client()
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["backend"] == "mock"


def test_chat_returns_openai_shape_and_usage():
    r = chat(make_client(), "ping", model="tiny")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "tiny"
    assert "ping" in body["choices"][0]["message"]["content"]
    usage = body["usage"]
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_default_model_is_used():
    r = chat(make_client(default_model="llama3.2:1b"))
    assert r.json()["model"] == "llama3.2:1b"


def test_auth_required_when_keys_configured():
    client = make_client(gateway_api_keys=frozenset({"secret-1"}))
    assert chat(client).status_code == 401
    assert chat(client, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert chat(client, headers={"Authorization": "Bearer secret-1"}).status_code == 200


def test_prompt_too_large_is_rejected():
    r = chat(make_client(max_prompt_chars=10), "x" * 11)
    assert r.status_code == 413


def test_invalid_payload_is_422():
    client = make_client()
    assert client.post("/v1/chat/completions", json={"messages": []}).status_code == 422
    assert chat(client, temperature=5).status_code == 422


def test_metrics_are_exported():
    client = make_client(price_per_1k_prompt=1.0, price_per_1k_completion=1.0)
    chat(client, "count me", model="metrics-model")
    text = client.get("/metrics").text
    assert 'llm_requests_total{backend="mock",model="metrics-model",status="ok"}' in text
    assert 'llm_tokens_total{direction="prompt",model="metrics-model"}' in text
    assert 'llm_cost_usd_total{model="metrics-model"}' in text
    assert "llm_request_duration_seconds_bucket" in text


class FailingBackend(MockBackend):
    async def complete(self, *args, **kwargs):
        raise BackendError("boom")

    async def ready(self):
        return False


def test_upstream_error_maps_to_502_and_not_ready():
    client = make_client(FailingBackend())
    assert chat(client).status_code == 502
    assert client.get("/readyz").status_code == 503


@pytest.mark.anyio
async def test_openai_backend_parses_upstream_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer up-key"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hi there"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    settings = Settings(upstream_api_key="up-key")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://upstream/v1",
        headers={"Authorization": "Bearer up-key"},
    )
    backend = OpenAICompatibleBackend(settings, client=client)
    text, p, c = await backend.complete("m", [{"role": "user", "content": "hi"}], 16, 0.2)
    assert (text, p, c) == ("hi there", 7, 3)


@pytest.mark.anyio
async def test_openai_backend_raises_on_http_error():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500)), base_url="http://u/v1"
    )
    backend = OpenAICompatibleBackend(Settings(), client=client)
    with pytest.raises(BackendError):
        await backend.complete("m", [{"role": "user", "content": "hi"}], 16, 0.2)


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd" * 10) == 10


@pytest.fixture
def anyio_backend():
    return "asyncio"
