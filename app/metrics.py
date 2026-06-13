"""Prometheus metrics definitions for the customer service chatbot.

Import these objects from other modules to record observations.
The metrics endpoint is exposed at /metrics by main.py when METRICS_ENABLED=true.
"""

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Request counters
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "chatbot_requests_total",
    "Total number of requests processed",
    ["status"],  # labels: allowed | blocked | error
)

# ---------------------------------------------------------------------------
# Request latency histogram
# ---------------------------------------------------------------------------
REQUEST_LATENCY = Histogram(
    "chatbot_request_duration_seconds",
    "End-to-end request latency in seconds (including guardrails + LLM)",
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30],
)

# ---------------------------------------------------------------------------
# Block reason counters
# ---------------------------------------------------------------------------
BLOCK_REASON_COUNT = Counter(
    "chatbot_blocks_total",
    "Number of requests blocked, broken down by guardrail stage",
    ["reason"],  # labels: input_validation | prompt_injection | policy_violation | output_moderation
)

# ---------------------------------------------------------------------------
# LLM call latency histogram
# ---------------------------------------------------------------------------
LLM_LATENCY = Histogram(
    "chatbot_llm_duration_seconds",
    "Time spent waiting for the LLM to generate a response",
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10, 30],
)

# ---------------------------------------------------------------------------
# Active requests gauge
# ---------------------------------------------------------------------------
ACTIVE_REQUESTS = Gauge(
    "chatbot_active_requests",
    "Number of requests currently being processed",
)
