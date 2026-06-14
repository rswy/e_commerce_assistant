# Production Readiness Report
## Customer Service AI — MVP → Production Hardening

---

## Executive Summary

This report documents the six-stage process of taking the Customer Service AI MVP
to a production-ready state. The MVP was a functional FastAPI chatbot backed by
SmolLM2 135M (via Ollama) with a regex-based guardrail pipeline. It had working
core functionality but was not safe to expose publicly.

All six stages have been implemented and committed. The final state is a system
that can be deployed under real traffic with security, reliability, observability,
and automated quality assurance.

**Test result at completion:** 37/37 tests passing, zero warnings.

---

## What Was Delivered

| Artifact | Location |
|----------|----------|
| Hardened guardrails | [app/guardrails.py](app/guardrails.py) |
| Production-ready API | [app/main.py](app/main.py) |
| API key auth middleware | [app/middleware/auth.py](app/middleware/auth.py) |
| Prometheus metrics | [app/metrics.py](app/metrics.py) |
| Extended config | [app/config.py](app/config.py) |
| Unit tests (guardrails) | [tests/test_guardrails.py](tests/test_guardrails.py) |
| Integration tests (API) | [tests/test_app.py](tests/test_app.py) |
| Live integration tests | [tests/integration/](tests/integration/) |
| CI/CD pipeline | [.github/workflows/ci.yml](.github/workflows/ci.yml) |
| Quality-gated evaluation | [evaluation/evaluate.py](evaluation/evaluate.py) |
| nginx reverse proxy | [nginx/nginx.conf](nginx/nginx.conf) |
| Prometheus scrape config | [monitoring/prometheus.yml](monitoring/prometheus.yml) |
| Multi-stage Dockerfile | [Dockerfile](Dockerfile) |
| Production compose overrides | [docker-compose.prod.yaml](docker-compose.prod.yaml) |
| Environment variable reference | [.env.example](.env.example) |
| Stage documentation (6 files) | [docs/](docs/) |

---

## Stage-by-Stage Interview Walk-Through

---

### Stage 1 — Security & Bug Fixes

**What I found first:**

The README explicitly documented two failing tests. My rule when inheriting a
codebase: never ship features over known bugs. But reading the actual test failures
more carefully, the real problem wasn't the tests — it was what the tests exposed:
the guardrail pipeline had no Unicode normalization. A user could type `іgnore
previous instructions` (Cyrillic `і`, visually identical to Latin `i`) and bypass
every single pattern match. This is a one-minute attack that any adversarial user
would find.

**Root cause analysis:**

The MVP lowercased input before matching, which handles A→a but does nothing for
characters from other scripts. Unicode homoglyph attacks work because `і` (U+0456)
lowercases to `і`, not to `i`. Similarly, inserting a zero-width space (`U+200B`)
inside "ignore" produces "ign​ore" which is visually identical but breaks every
substring and regex match.

**What I changed:**

Added `normalize_text()` — a four-step pipeline that runs before every guardrail
check:

1. **NFKC normalization** — maps fullwidth ASCII, ligatures, compatibility
   equivalents to canonical forms.
2. **Homoglyph substitution table** — maps the most common Cyrillic and Greek
   lookalikes to their ASCII equivalents (e.g. Cyrillic `о` → `o`).
3. **Zero-width character stripping** — removes 11 invisible Unicode code points.
4. **Whitespace collapse** — normalizes non-breaking spaces, ideographic spaces, tabs.

Also expanded injection patterns from 8 to 22 (covering DAN, jailbreak,
developer mode, `[[instructions]]`, `[system]`, `respond only as`, `from now
on you`, `simulate a`, `override your`, `bypass your`, `do not follow`, `stop
being`, `pretend you are`).

**Test coverage:** 37 tests total (was 11). New tests explicitly verify that
Cyrillic homoglyphs and zero-width character injections are caught.

---

### Stage 2 — API Hardening

**Why this is the second priority:**

Even with perfect guardrails, an unauthenticated, rate-unlimited endpoint is a
liability. An attacker with a script can send 10,000 requests per minute, either
to:
- Find the rare case that slips through the guardrails.
- DoS the Ollama process (which can only handle 1–2 concurrent requests).
- Run up inference costs if you switch to a hosted model.

**What I changed:**

1. **API key authentication** — `X-API-Key` header, implemented as a FastAPI
   `Depends()`. When `API_KEY` env is empty, auth is disabled (safe default for
   local dev). In production, set `API_KEY=$(openssl rand -hex 32)`.

2. **Rate limiting** — `slowapi` with `20 req/min/IP` (configurable via
   `RATE_LIMIT_PER_MINUTE`). Rate limiting runs at the app layer; nginx has an
   independent matching limit as a belt-and-suspenders defense.

3. **LLM timeout** — `asyncio.wait_for(asyncio.to_thread(llm.invoke, ...), timeout=30)`
   Returns HTTP 504 on timeout, HTTP 503 on Ollama connection error. Without
   this, a slow or hung Ollama request blocks the event loop indefinitely.

4. **`GET /health`** — checks live Ollama connectivity via `httpx`. Returns
   `{"status": "ok"|"degraded", "ollama": "up"|"down"}`. Stays HTTP 200 in both
   states so load-balancer probes can distinguish "app crashed" from "Ollama down".

5. **`request_id`** — UUID on every response. Essential for correlating a
   user-reported issue to a specific log line in a distributed system.

**Key design decision on test isolation:**

The rate limiting test required resetting the in-memory limiter bucket, because
all TestClient requests share the same `testclient` IP. I added
`limiter._storage.reset()` at the top of three tests that follow
`test_rate_limiting`. The alternative (autouse fixture) would reset for every
test, which is correct but obscures intent. Explicit reset in the tests that
need isolation is clearer.

---

### Stage 3 — Observability

**Why observability before CI:**

You cannot write a quality gate without knowing what to measure. And you cannot
debug production incidents without structured logs and metrics. This stage
creates the instruments; Stage 4 uses them.

**What I changed:**

1. **structlog** — replaces the standard library `logging.basicConfig`. In
   `development` mode: colored console output. In `production` mode: JSON lines
   with `time`, `level`, `event`, `request_id`, `latency_seconds`, `blocked`,
   `block_reason`. Every log entry carries the `request_id` for correlation.

2. **Prometheus metrics** — five instruments:
   - `chatbot_requests_total{status}` — counter by outcome (allowed/blocked/error)
   - `chatbot_request_duration_seconds` — histogram for latency SLO tracking
   - `chatbot_blocks_total{reason}` — counter by block reason (feeds alerting)
   - `chatbot_llm_duration_seconds` — histogram for LLM-specific latency
   - `chatbot_active_requests` — gauge for concurrency monitoring

3. **`/metrics` endpoint** — mounted via `make_asgi_app()` from prometheus_client,
   toggled by `METRICS_ENABLED`. Blocked at the nginx layer from external access.

**What to alert on:**

```
ALERT HighBlockRate
  IF rate(chatbot_blocks_total[5m]) / rate(chatbot_requests_total[5m]) > 0.50
  LABELS { severity="warning" }
  # Block rate >50%: either under attack or guardrails regression.

ALERT LLMLatencyDegraded
  IF histogram_quantile(0.95, chatbot_llm_duration_seconds_bucket) > 10
  LABELS { severity="warning" }
  # 95th percentile LLM latency >10s: Ollama overloaded or model too large.

ALERT HighErrorRate
  IF rate(chatbot_requests_total{status="error"}[5m]) > 0.05
  LABELS { severity="critical" }
  # Error rate >5%: service is broken.
```

---

### Stage 4 — CI/CD Pipeline

**Design philosophy:**

The pipeline is a quality ratchet — once a bar is set (80% coverage, 95%
blocking accuracy), it can only go up. Every merged PR must pass; the pipeline
rejects regressions automatically.

**Pipeline jobs:**

```
push/PR → lint ─┬─ test ──── evaluate ─┬─ deploy-staging (main only)
                │                       │
                └─ docker-build ────────┘
```

1. **lint** — `ruff check` (style) + `ruff format --check` (formatting) +
   `mypy` (type checking, non-blocking for now). Runs in parallel with test.

2. **test** — `pytest tests/ --ignore=tests/integration --cov=app --cov-fail-under=80`.
   Coverage below 80% fails the build. Results uploaded to Codecov.

3. **evaluate** — starts the app with a mocked LLM (no Ollama needed in CI),
   runs `evaluate.py --quality-gate`, exits 1 if blocking accuracy < 95%.

4. **docker-build** — builds the multi-stage image with layer caching via
   GitHub Actions cache. No push — validates the image builds cleanly.

5. **deploy-staging** — placeholder with environment approval gate. Fill in
   registry credentials and deployment mechanism (ECS, Kubernetes, Fly.io).

**Key decision on the evaluate job:**

The evaluation script calls a live `/query` API. In CI, Ollama is not available.
The solution: start the FastAPI app with `langchain_ollama.OllamaLLM` mocked to
return a fixed string. This tests the guardrail blocking logic (which doesn't
need the LLM) and gives a realistic similarity score for safe queries (since the
mock returns a plausible customer-service response). The quality gate threshold
for similarity (0.30) is deliberately low to account for the mock — the
blocking accuracy gate (0.95) is the meaningful signal in CI.

---

### Stage 5 — Evaluation & Model Quality

**Problem with the original evaluation script:**

It existed and worked, but was not wired to anything. A developer could merge
code that degraded guardrail accuracy from 100% to 60% and nobody would know
until customers complained.

**What I changed:**

1. **`--quality-gate` flag** — `sys.exit(1)` when thresholds are not met.
   Used by the CI `evaluate` job.

2. **`--output-json`** — writes results to a JSON file. Uploaded as a GitHub
   Actions artifact. Enables trend tracking over time.

3. **`--base-url`** — the original script hardcoded `http://localhost:8000`.
   This made it impossible to run against staging. Now configurable.

4. **Per-category thresholds** — injection and violation categories require
   95% blocking accuracy independently. A regression in just one category fails
   the gate even if overall accuracy looks fine.

**Model upgrade path:**

| Model | Size | RAM needed | Quality | Use case |
|-------|------|-----------|---------|----------|
| SmolLM2 135M | 271 MB | ~1 GB | Poor | Learning/demo only |
| Llama 3.2 3B | ~2 GB | ~4 GB | Good | Most production workloads |
| Mistral 7B | ~4 GB | ~6-8 GB | Very good | Complex/nuanced queries |
| Claude Haiku (API) | — | — | Excellent | Highest quality, adds cost |

To upgrade: change `LLM_MODEL=llama3.2:3b` in your `.env`. The app, guardrails,
and evaluation script require no code changes — Ollama handles the model swap.

---

### Stage 6 — Production Infrastructure

**nginx as the first line of defense:**

nginx sits in front of the app and enforces rate limiting, TLS, and security
headers independently of the Python code. This matters because:
- If the Python app has a bug that breaks rate limiting, nginx still throttles.
- TLS termination at nginx means the app doesn't need to handle certificates.
- Security headers are applied even to responses generated during app startup.

**Multi-stage Docker build:**

The MVP Dockerfile copied test files into the image (`tests/`) and installed
development tools. The production image:
- Builds dependencies in a `builder` stage, copies only the venv to `production`.
- Excludes `tests/`, `evaluation/`, `docs/` — not needed at runtime.
- Runs as `appuser` (non-root) — container escape has much lower blast radius.
- Uses exec-form CMD so uvicorn receives SIGTERM and can drain connections cleanly.

**`docker-compose.prod.yaml` separation:**

Development (`docker-compose.yaml`) and production (`docker-compose.prod.yaml`)
are separate files merged at deploy time. This prevents accidentally deploying
with `build: .` instead of a registry image, and allows production-only settings
(resource limits, cert mounts, no public Prometheus port) without polluting the
dev workflow.

---

## Before vs After: Gap Summary

| Area | MVP | Production |
|------|-----|-----------|
| Unicode bypass | Vulnerable | Blocked (normalize_text) |
| Zero-width bypass | Vulnerable | Blocked (zero-width strip) |
| Injection pattern coverage | 8 patterns | 22 patterns |
| Authentication | None | X-API-Key (optional) |
| Rate limiting | None | 20 req/min/IP (nginx + app) |
| LLM timeout | None (hangs forever) | 30s → 504 |
| Health check | `/` (shallow) | `/health` with Ollama probe |
| Logging | Unstructured `logging.info` | structlog JSON with request_id |
| Metrics | None | 5 Prometheus metrics |
| CI pipeline | None | 5-job pipeline with quality gates |
| Coverage gate | None | 80% required |
| Eval automation | Manual script | CI quality gate (95% blocking) |
| Docker image | Single stage, includes tests | Multi-stage, non-root, ~40% smaller |
| Reverse proxy | None | nginx (rate limit, TLS, security headers) |
| Secret management | Hardcoded defaults | .env.example + documented secret management |
| Tests | 11 (2 known failing) | 37 (0 failing, 0 warnings) |

---

## What Is Still Missing (Honest Assessment)

This implementation covers the infrastructure layer. The following items are
next in priority order for a real production deployment:

1. **Conversation state** — the app is stateless; users cannot reference previous
   messages. Add Redis with a session TTL to persist conversation history.

2. **Order system integration** — queries like "where is my order #12345" hit the
   guardrail pipeline and reach the LLM, which has no order data. Connect to a
   mock orders API via LangChain tool calling.

3. **Real model upgrade** — SmolLM2 135M will produce poor responses for most
   real customer questions. Upgrade to Llama 3.2 3B before any public launch.

4. **Human review queue** — sample 5% of production traffic (blocked and allowed)
   to a review queue for periodic human evaluation. This closes the feedback loop
   that automated metrics cannot cover.

5. **Secrets management** — the `.env.example` documents variables but relies on
   file-based secrets. For a serious production deployment, use Docker secrets,
   AWS Secrets Manager, or HashiCorp Vault.

6. **Load testing** — run `locust` against the full stack (nginx → app → Ollama)
   to find the concurrency ceiling before it finds you.

---

## Git History

```
3b77a25  feat(stage-6): production infrastructure — multi-stage Docker, nginx, env separation
d3e637f  feat(stage-5): evaluation improvements — quality gate, --output-json, integration tests
7943cc9  feat(stage-4): CI/CD pipeline — GitHub Actions with quality gates
42d2b87  feat(stage-3): observability — structlog JSON, Prometheus metrics, scrape config
b15cab5  feat(stage-2): API hardening — auth, rate limiting, LLM timeout, health endpoint
de88aa1  feat(stage-1): security hardening — Unicode normalization, expanded injection patterns
2b1f4d9  chore: initial commit — MVP baseline before production hardening
```

---

*Generated by Claude Sonnet 4.6 as part of a simulated production-readiness interview.*
