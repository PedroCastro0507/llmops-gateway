"""Prometheus metrics exposed on /metrics."""

from prometheus_client import Counter, Gauge, Histogram

REQUESTS = Counter(
    "llm_requests_total",
    "Chat completion requests handled by the gateway.",
    ["model", "backend", "status"],
)

LATENCY = Histogram(
    "llm_request_duration_seconds",
    "End-to-end latency of chat completion requests.",
    ["model", "backend"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60),
)

TOKENS = Counter(
    "llm_tokens_total",
    "Tokens processed, split by direction (prompt or completion).",
    ["model", "direction"],
)

COST = Counter(
    "llm_cost_usd_total",
    "Estimated spend in USD, from the configured per-1K-token prices.",
    ["model"],
)

INFLIGHT = Gauge(
    "llm_inflight_requests",
    "Requests currently waiting on the upstream model.",
)
