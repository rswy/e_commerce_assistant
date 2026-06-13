# Stage 3 — Observability

## Metrics Reference

All metrics are exposed at `GET /metrics` in Prometheus text format when
`METRICS_ENABLED=true`.

| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `chatbot_requests_total` | Counter | `status` | Total requests processed. `status` is one of `allowed`, `blocked`, `error`. |
| `chatbot_request_duration_seconds` | Histogram | — | End-to-end latency including all guardrail steps and the LLM call. Buckets: 50 ms – 30 s. |
| `chatbot_blocks_total` | Counter | `reason` | Requests blocked by a guardrail stage. `reason` is one of `input_validation`, `prompt_injection`, `policy_violation`, `output_moderation`. |
| `chatbot_llm_duration_seconds` | Histogram | — | Time spent inside the LLM call only (excludes guardrails). Buckets: 100 ms – 30 s. |
| `chatbot_active_requests` | Gauge | — | Number of requests currently in-flight. Useful for detecting concurrency buildup. |

### Useful PromQL Queries

```promql
# Request rate per minute
rate(chatbot_requests_total[1m]) * 60

# Error rate
rate(chatbot_requests_total{status="error"}[5m])
  / rate(chatbot_requests_total[5m])

# Blocking rate by reason
rate(chatbot_blocks_total[5m])

# 95th percentile request latency
histogram_quantile(0.95, rate(chatbot_request_duration_seconds_bucket[5m]))

# 95th percentile LLM latency
histogram_quantile(0.95, rate(chatbot_llm_duration_seconds_bucket[5m]))
```

## Log Format

When `APP_ENV=production`, all logs are emitted as single-line JSON objects.
Each log line contains at minimum:

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | ISO 8601 string | UTC time of the log event |
| `level` | string | `info`, `warning`, `error`, `debug` |
| `event` | string | Machine-readable event name (e.g. `request_received`) |
| `request_id` | UUID string | Unique per-request identifier for correlation |
| `session_id` | string or null | Client-supplied session identifier |
| `question_length` | integer | Length of the input question in characters |
| `blocked` | boolean | Whether the request was blocked by a guardrail |
| `block_reason` | string or null | Human-readable block reason (null if not blocked) |
| `latency_seconds` | float | End-to-end wall-clock time for the request |

Example production log line:

```json
{
  "timestamp": "2026-06-13T10:23:45.123Z",
  "level": "info",
  "event": "request_completed",
  "request_id": "b3d2c1e0-1234-5678-abcd-ef0123456789",
  "session_id": null,
  "blocked": false,
  "block_reason": null,
  "latency_seconds": 1.432
}
```

In development (`APP_ENV=development`), `structlog` renders colored console
output instead.

## Dashboards

Recommended Grafana dashboard panels (import via JSON or build manually):

1. **Request Rate** — `rate(chatbot_requests_total[1m])` split by `status`
2. **Block Reason Breakdown** — `rate(chatbot_blocks_total[5m])` split by `reason`
3. **Latency Heatmap** — `chatbot_request_duration_seconds_bucket`
4. **LLM Latency p50/p95/p99** — `histogram_quantile` on `chatbot_llm_duration_seconds_bucket`
5. **Active Requests** — `chatbot_active_requests`
6. **Error Rate** — computed from `chatbot_requests_total{status="error"}`

## Alerting Rules (Suggestions)

Add these to a `alert_rules.yml` and reference it from `monitoring/prometheus.yml`:

```yaml
groups:
  - name: chatbot-alerts
    rules:
      - alert: HighErrorRate
        expr: |
          rate(chatbot_requests_total{status="error"}[5m])
          / rate(chatbot_requests_total[5m]) > 0.05
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "Error rate above 5% for 2 minutes"

      - alert: HighBlockRate
        expr: |
          rate(chatbot_blocks_total[5m])
          / rate(chatbot_requests_total[5m]) > 0.30
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "More than 30% of requests are being blocked"

      - alert: SlowLLMResponses
        expr: |
          histogram_quantile(0.95, rate(chatbot_llm_duration_seconds_bucket[5m])) > 10
        for: 3m
        labels:
          severity: warning
        annotations:
          summary: "LLM p95 latency exceeded 10 seconds"

      - alert: ServiceDown
        expr: up{job="chatbot-api"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Chatbot API is unreachable by Prometheus"
```

## How to View in Phoenix vs Prometheus

### Phoenix (LangChain traces)

Enable with `PHOENIX_ENABLED=true` and `PHOENIX_ENDPOINT=http://localhost:6006`.

Phoenix captures span-level tracing for every guardrail stage:
- `customer_query` — top-level span per request
- `input_filtering`, `prompt_injection_detection`, `policy_violation_detection`
- `llm_call` — includes LangChain-level spans with token counts
- `output_moderation`

Access the Phoenix UI at `http://localhost:6006`.

### Prometheus + Grafana

1. Start the stack: `docker compose --profile full up`
2. Prometheus UI: `http://localhost:9090`
3. Metrics endpoint: `http://localhost:8000/metrics` (internal only via nginx)
4. To add Grafana: add a `grafana` service to `docker-compose.yaml` and point
   its datasource at `http://prometheus:9090`.
