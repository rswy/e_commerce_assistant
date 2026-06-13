# Stage 2 — API Hardening

## Changes Made

### Authentication — `app/middleware/auth.py`

API key authentication is implemented as a FastAPI dependency.  The key is
passed in the `X-API-Key` request header.

- When `API_KEY` env var is **empty** (default), auth is **disabled** — every
  request is allowed through (development / local mode).
- When `API_KEY` is set to a non-empty string, requests without the correct key
  receive **HTTP 403 Forbidden**.

### Rate Limiting — `slowapi`

Per-IP rate limiting is applied at the application layer using `slowapi`, which
wraps Python's `limits` library.

- Default: 20 requests per minute per IP address (mirrors the nginx zone).
- Exceeding the limit returns **HTTP 429 Too Many Requests**.
- The limit is configurable via `RATE_LIMIT_PER_MINUTE` env var.

nginx adds a second independent rate-limiting layer (`limit_req_zone`) so that
requests never even reach the application for obvious burst attacks.

### LLM Timeout — `asyncio.wait_for`

LangChain's `OllamaLLM.invoke` is a blocking synchronous call.  In the MVP it
could block the event loop indefinitely if Ollama was slow.

The fix wraps the call with `asyncio.to_thread` (moves the blocking call off
the event loop) and `asyncio.wait_for` (applies a deadline):

```python
response_text = await asyncio.wait_for(
    asyncio.to_thread(llm.invoke, full_prompt),
    timeout=LLM_TIMEOUT_SECONDS,
)
```

On timeout: **HTTP 504 Gateway Timeout** with a descriptive message.
On Ollama connection error: **HTTP 503 Service Unavailable**.

### Health Endpoint — `GET /health`

A dedicated health check that actively tests Ollama connectivity:

```json
{
  "status": "ok",          // or "degraded"
  "ollama": "up",          // or "down"
  "model": "smollm2:135m"
}
```

Always returns HTTP 200 so load-balancer health checks can distinguish a crashed
app (5xx) from a working app with a degraded upstream.

## Configuration Reference

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `API_KEY` | `""` | API key for X-API-Key header. Empty = auth disabled. |
| `RATE_LIMIT_PER_MINUTE` | `20` | Max requests per minute per IP. |
| `LLM_TIMEOUT_SECONDS` | `30` | Seconds before LLM call times out (504). |
| `APP_ENV` | `development` | `development` or `production`. Controls log format. |
| `LOG_LEVEL` | `INFO` | Python log level. |
| `METRICS_ENABLED` | `true` | Expose `/metrics` endpoint for Prometheus. |

## How to Enable Auth

```bash
# Generate a strong key
export API_KEY=$(openssl rand -hex 32)

# Pass it in requests
curl -H "X-API-Key: $API_KEY" http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is your return policy?"}'
```

In Docker Compose, add `API_KEY` to the `app` service environment or use a
Docker secret / `.env` file (never commit the actual key to git).

## How to Test Rate Limiting

```bash
# Fire 21 requests — the 21st should 429
for i in $(seq 1 21); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    http://localhost:8000/query \
    -H "Content-Type: application/json" \
    -d '{"question":"test"}')
  echo "Request $i: $STATUS"
done
```

Or using the automated test:

```bash
pytest tests/test_app.py::test_rate_limiting -v
```

## Failure Modes & Fallbacks

| Failure | HTTP Status | Behaviour |
|---------|-------------|-----------|
| Missing / wrong API key | 403 | Request rejected before guardrails |
| Rate limit exceeded | 429 | `slowapi` returns Retry-After header |
| LLM timeout | 504 | User gets actionable "try again" message |
| Ollama connection refused | 503 | User gets "service unavailable" message |
| Unhandled exception | 500 | Generic error; full details in structured log |
| Prompt injection detected | 200 (blocked=true) | Guardrail blocks, LLM not called |
| Policy violation detected | 200 (blocked=true) | Guardrail blocks, LLM not called |
