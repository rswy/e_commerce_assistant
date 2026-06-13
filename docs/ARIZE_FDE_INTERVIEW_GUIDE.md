# Arize AI FDE Interview Guide
## Taking a Toy MVP to Production-Ready Multi-Agent AI

---

> **What this document is for:** Walk through this before the interview. The goal
> is not to memorise answers — it is to give you a coherent narrative spine and
> specific evidence from the codebase so you can answer any follow-up question
> with real detail. Treat it like a surgical debrief, not a script.

---

## 1. The Two-Minute Pitch

> "I started with an open-source customer service chatbot — a FastAPI app running
> SmolLM2 135M locally via Ollama with a five-step regex guardrail pipeline.
> It was functional as a demo but had serious production gaps: trivially bypassed
> security, no auth, no timeout, no conversation history, the LLM had no access
> to real data, and the only evaluation was a manual script nobody ran.
>
> I took it through six production-hardening stages and then extended it into a
> real multi-agent system. Each stage was committed separately so you can see the
> engineering decisions one at a time. The result has a layered security model,
> a multi-agent orchestration layer with tool-augmented agents, conversation
> session state, a human review queue, structured telemetry wired through Phoenix
> for every request, and a CI pipeline that gates deployments on both test
> coverage and guardrail accuracy.
>
> The reason I think this is directly relevant to Arize is that observability
> was not an afterthought — it was the mechanism that made quality assurance
> possible. Without tracing, you cannot know if your guardrails are being bypassed.
> Without evaluation metrics in CI, model regressions ship silently. This project
> is a working demo of why observability and evaluation are first-class engineering
> concerns for LLM systems, not nice-to-haves."

**Keep it at two minutes. Stop. Let them ask.**

---

## 2. Architecture Walk-Through

Use this for technical deep dives. Reference specific files.

```
Internet
    │
    ▼
nginx :80/:443
  ├─ rate limiting (20 req/min per IP)
  ├─ security headers (HSTS, CSP, X-Frame-Options)
  ├─ /metrics → blocked externally
  └─ proxy_pass → app:8000
         │
         ▼
FastAPI app:8000
  ├─ verify_api_key (X-API-Key header, optional)
  ├─ slowapi rate limiter (20 req/min per IP, in-memory)
  │
  └─ POST /query pipeline
         │
     [Step 1] filter_input()
         │  └─ normalize_text(): NFKC + homoglyph + zero-width + whitespace
         │
     [Step 2] detect_prompt_injection()
         │  └─ 22 regex patterns, re.IGNORECASE, applied to normalized text
         │
     [Step 3] detect_policy_violation()
         │  └─ harmful / personal-data / off-topic keyword sets
         │
     [Step 4] CustomerServiceOrchestrator
         │
         ├─ IntentClassifier → Intent + entities (order_id, product_id)
         │
         ├─ OrderAgent        ← get_order(order_id) from mock_orders.json
         ├─ ReturnsAgent      ← check_return_eligibility(order)
         ├─ ProductAgent      ← search_products(query) from mock_products.json
         ├─ EscalationAgent   → always enqueues to ReviewQueue
         └─ GeneralAgent      ← fallback
         │
         ├─ ConversationSessionStore (in-memory, async, Redis-ready)
         └─ InMemoryReviewQueue (flagged items for human review)
         │
     [Step 5] moderate_output()
         │  └─ system-leak phrases, SSN/CC patterns, roleplay persona detection
         │
         └─ QueryResponse (answer, intent, agent_name, tools_called,
                           needs_review, session_id, request_id)

Observability:
  ├─ structlog JSON on every request (request_id, latency, intent, agent, tools)
  ├─ Phoenix traces: orchestrator → intent_classification → agent:X spans
  ├─ Prometheus metrics: REQUEST_COUNT, REQUEST_LATENCY, BLOCK_REASON_COUNT,
  │                       LLM_LATENCY, ACTIVE_REQUESTS
  └─ LLM-as-judge (claude-haiku-4-5) in CI: accuracy, helpfulness, tone, completeness
```

**Files to reference:**
- [app/agents/orchestrator.py](../app/agents/orchestrator.py) — routing logic
- [app/agents/intent_classifier.py](../app/agents/intent_classifier.py) — classification
- [app/guardrails.py](../app/guardrails.py) — the 5-step pipeline
- [app/main.py](../app/main.py) — how it all connects
- [evaluation/llm_evaluator.py](../evaluation/llm_evaluator.py) — LLM-as-judge

---

## 3. The Challenges — Specific, Defensible

These are the moments interviewers probe. Have the detail ready.

---

### Challenge 1: Unicode Homoglyph Bypass

**What happened:** The guardrail pipeline lowercased text before regex matching.
This works for A→a but does nothing for Cyrillic `і` (U+0456), which is visually
identical to Latin `i` but is a different Unicode code point. `іgnore previous
instructions` bypassed every pattern match.

**How I diagnosed it:** I was reading the guardrail tests and noticed they only
tested ASCII injection strings. I thought: what about homoglyphs? I tested
`іgnore previous instructions` with Cyrillic `і` — it passed through. Then I
checked zero-width space: `ign​ore previous instructions` (U+200B inside) — also
passed.

**What I built:** `normalize_text()` in [app/guardrails.py](../app/guardrails.py):
1. NFKC normalization (handles fullwidth ASCII, ligatures)
2. A manual confusable table for the most common Cyrillic/Greek lookalikes
   (because NFKC does NOT handle cross-script confusables — it only normalizes
   within a script)
3. Stripping of 11 invisible Unicode code points (zero-width space, soft hyphen, etc.)
4. Whitespace collapse (handles non-breaking spaces, ideographic spaces)

**Why not just use the Unicode TR39 confusables dataset?**
It's 7 MB, updated infrequently, and the most common attack vectors are a small
subset. I implemented the 20 most common Cyrillic/Greek lookalikes — enough to
catch real attacks without the operational overhead. For a system handling
financial data I'd use the full dataset.

**How to verify:**
```python
from app.guardrails import detect_prompt_injection
detect_prompt_injection("іgnore previous instructions")  # Cyrillic і
# → (True, 'Prompt injection attempt detected')
```

---

### Challenge 2: Rate Limiter Test Isolation

**What happened:** The rate limiting test (`test_rate_limiting`) sent 21 requests
and expected the 21st to return 429. But earlier tests in the same pytest session
had already made requests using the same `testclient` IP, consuming part of the
budget. The test was flaky — it passed or failed depending on test ordering.

**Root cause:** slowapi's in-memory storage keyed by IP. All TestClient requests
use `testclient` as the remote address. The bucket is shared across the entire
test session.

**Fix:** Reset `limiter._storage` at the top of each test that needs a clean
rate-limit counter. This is more explicit than an autouse fixture — it makes the
isolation intent clear at the point of use.

```python
from app.main import limiter
if hasattr(limiter, "_storage"):
    limiter._storage.reset()
```

**Design note for production:** In a multi-replica deployment, in-memory rate
limiting doesn't work — each instance has its own counter. Fix: switch slowapi
to a Redis backend (`from limits.storage import RedisStorage`). The REDIS_URL
config var is already wired in for this purpose.

---

### Challenge 3: Asyncio + Synchronous LangChain

**What happened:** LangChain's `OllamaLLM.invoke()` is a blocking synchronous
call. FastAPI runs on an async event loop (uvicorn + asyncio). A blocking call
in an async handler blocks the entire event loop — no other requests can be
served while the LLM is thinking.

**Fix:** `asyncio.to_thread(llm.invoke, prompt)` — offloads the blocking call to
a thread pool, freeing the event loop. Wrapped in `asyncio.wait_for()` with a
configurable timeout so a hung Ollama process doesn't block indefinitely:

```python
response_text = await asyncio.wait_for(
    asyncio.to_thread(llm.invoke, full_prompt),
    timeout=LLM_TIMEOUT_SECONDS,
)
```

Returns HTTP 504 on timeout, HTTP 503 on connection error.

**Test challenge:** Mocking this correctly required properly closing the coroutine
before raising `TimeoutError`, otherwise Python emits "coroutine was never
awaited" warnings that break the test run:

```python
async def _timeout_wait_for(coro, timeout):
    coro.close()  # Properly close the unawaited coroutine
    raise asyncio.TimeoutError()
```

---

### Challenge 4: Module Reload Side Effects in Tests

**What happened:** The API key auth test used `importlib.reload()` to make the
auth module re-read `API_KEY` from environment. After the test, monkeypatch
restored the env var — but the reloaded module kept the new value permanently.
Subsequent tests saw `API_KEY = "super-secret-key"` and returned 403 on every
request.

**Fix:** Removed the `importlib.reload()` calls. Python's `unittest.mock.patch`
directly patches the module-level binding, which is what `verify_api_key` reads
at call time. No reload needed.

```python
with patch("app.middleware.auth.API_KEY", "super-secret-key"):
    # verify_api_key reads app.middleware.auth.API_KEY at call time
    response = test_client.post("/query", ...)
```

**Why this matters:** Module reloads in tests are a code smell. They mutate global
state that persists across tests, making failures order-dependent and hard to debug.

---

### Challenge 5: Small Model Limitations for Tool Calling

**What happened:** SmolLM2 135M cannot reliably emit structured tool call JSON.
Traditional LangChain tool use (which expects the model to output
`{"tool": "get_order", "args": {"order_id": "ORD-10001"}}`) fails unpredictably
with sub-1B models.

**Design decision:** Invert the pattern. Instead of asking the model to decide
what tools to call, use deterministic routing:

```
1. Intent classification (keyword-based, not LLM)
2. Entity extraction (regex, not LLM)
3. Tool call (deterministic, based on entities)
4. Inject tool results into LLM prompt as context
5. LLM generates natural language response only
```

The model's only job is to generate a helpful response given pre-fetched data.
This works with 135M models and scales to larger models without code changes.

**Tradeoff:** Less flexible than true agentic tool use. The model cannot chain
tool calls or decide to look up additional information mid-response. For a
customer service bot with known intent categories, this is fine. For a general
agent, you'd need a model capable of reliable function calling (Llama 3.1 8B+).

---

## 4. Design Decisions — Be Ready to Defend These

---

### "Why local SLM over a hosted LLM?"

**Answer:** Three reasons for this project specifically:

1. **Privacy:** Customer service queries contain PII (order numbers, names,
   addresses). Sending them to an external API requires data processing agreements
   and introduces a data exfiltration risk. On-premise keeps data entirely local.

2. **Cost at scale:** At 1000 queries/day, Claude Haiku costs ~$0.15/day.
   At 100k queries/day, that's $15/day just in inference. A local 3B model
   has zero marginal cost per query after the hardware is paid for.

3. **Latency:** For a chat application, sub-second responses matter. A local
   Mistral 7B on a decent GPU generates 30-50 tokens/second with <100ms TTFB.
   Hosted APIs add 100-400ms network latency before the first token.

**Honest limitation:** SmolLM2 135M is too small for production. The right model
for this use case is Llama 3.2 3B (same local setup, 10x better quality).
The architecture supports upgrading with a single env var: `LLM_MODEL=llama3.2:3b`.

---

### "Why regex guardrails instead of a classifier?"

**Answer:** Interpretability, speed, and zero dependencies.

- A regex pattern is auditable by a non-ML engineer. You can explain exactly why
  a specific query was blocked.
- Pattern matching runs in microseconds. An ML classifier adds 10-50ms latency
  and requires a model to be loaded.
- No external API call, no model file to manage, no drift.

**When I'd use a classifier instead:** When the false-negative rate on regex is
too high (sophisticated injection that doesn't match patterns), or when I need to
classify at embedding-similarity level rather than keyword level. For a production
system with >$1M ARR at stake, I'd layer: regex first (cheap, fast, interpretable),
ML classifier second (catches what regex misses), Phoenix traces to monitor both.

---

### "Why intent-based routing instead of a single large-context agent?"

**Answer:** Reliability, debuggability, and cost efficiency.

- A single agent that handles all intents needs a large context window and a
  capable model. Intent-based routing means each agent is optimized for a narrow
  task with a short, focused prompt.
- When something goes wrong, the trace shows exactly which agent handled the
  request and what tools it called. Debugging a monolithic agent prompt is much
  harder.
- Cost: shorter prompts = fewer tokens = lower cost (for hosted models) or faster
  inference (for local models).

**Tradeoff:** The intent classifier can misroute. A query like "I want to return
my order but also check the status" touches two agents. The current system picks
the highest-confidence intent. A more sophisticated system would fan out to
multiple agents and synthesize responses.

---

### "Why in-memory session store instead of Redis?"

**Answer:** Correct starting point for the complexity level, with a clear upgrade path.

The in-memory store is wrapped in an async interface that is Redis-compatible.
The production upgrade is: add `redis>=5.0.0` (already in pyproject.toml),
implement `RedisSessionStore` with the same interface, swap in `get_session_store()`
at startup based on `REDIS_URL` env var. No callers need to change.

For a single-instance deployment, in-memory is sufficient. For multi-replica
(horizontal scaling), you need a shared external store — that's Redis.
REDIS_URL is already plumbed through config.py and docker-compose.yaml.

---

### "Why ship SmolLM2 135M when it's too small for production?"

**Answer:** The architecture is the deliverable, not the model weights.

The point of this project is to demonstrate that you can build a production-grade
AI system — with observability, evaluation, security, and reliability — regardless
of which model is running underneath. The model is a configuration variable.
Upgrading from SmolLM2 135M to Llama 3.2 3B to Mistral 7B to Claude Sonnet
requires changing exactly one env var: `LLM_MODEL`.

This is actually the core Arize value proposition: the system should be observable
enough that you can detect when the model quality is insufficient and make an
informed upgrade decision, rather than discovering it from customer complaints.

---

## 5. Observability Strategy — This Is Arize's Core Domain

---

### What We Capture at Each Layer

| Layer | Instrument | Data |
|-------|-----------|------|
| HTTP | structlog JSON | request_id, session_id, latency_ms, blocked, intent, agent, tools_called |
| Guardrails | Phoenix spans | input_filtering, prompt_injection, policy_violation, output_moderation |
| Orchestrator | Phoenix spans | intent_classification (with confidence), agent routing |
| Agent | Phoenix spans | agent name, tools called, tool results metadata |
| LLM | Phoenix spans (LangChain auto-instrumented) | prompt, completion, latency, token count |
| Metrics | Prometheus | REQUEST_COUNT, REQUEST_LATENCY, BLOCK_REASON_COUNT, LLM_LATENCY, ACTIVE_REQUESTS |
| Evaluation (CI) | Artifact JSON | blocking_accuracy per category, cosine similarity |
| Evaluation (LLM judge) | Artifact JSON | accuracy, helpfulness, tone, completeness per intent |

---

### The Phoenix Trace for a Single Request

When a user asks "Where is my order ORD-10001?", this is what Phoenix shows:

```
customer_query                                       450ms
  ├─ orchestrator                                    440ms
  │    ├─ intent_classification                        2ms
  │    │    intent=order_status, confidence=0.9
  │    │    entities={order_id: ORD-10001}
  │    │
  │    └─ agent:order_agent                          438ms
  │         tools.called=[get_order]
  │         tool_results.order_found=True
  │         needs_review=False
  │
  └─ output_moderation                                 1ms
       guardrail.passed=True
```

This trace tells you:
- Which agent handled the query
- What tools were called and whether they found data
- Where the latency budget was spent (is it the tool lookup or the LLM?)
- Whether the output moderation fired

**This is exactly the kind of trace data Arize/Phoenix is designed to surface.**
In a production deployment, you'd alert on:
- High block rates (attack pattern or guardrail regression?)
- Agent mismatch (intent classifier routing incorrectly?)
- Tool miss rate (order_found=False — customer gave wrong order ID or data is stale?)
- LLM latency spikes (model overloaded or model upgraded to larger variant?)

---

### The Evaluation Framework

Three evaluation layers, each measuring different things:

**Layer 1 — Blocking accuracy (CI gate, automated)**
```
blocking_accuracy = correctly_blocked / total_queries
threshold = 95%  ← CI fails if below this
```
Catches guardrail regressions immediately on every PR.

**Layer 2 — Semantic similarity (CI gate, automated)**
```
similarity = cosine_similarity(
    embed(response),
    embed(ground_truth)
)
threshold = 30%  ← deliberately low because of mocked LLM in CI
```
In production with the real model, target >60%.

**Layer 3 — LLM-as-judge (CI gate, human-interpretable)**
```
judge = claude-haiku-4-5-20251001
dimensions = [accuracy, helpfulness, tone, completeness]
threshold = 60% overall  ← CI warns if below this
```
The judge model evaluates things cosine similarity cannot: tone, empathy,
whether the response actually answers the question. This is the closest
automated proxy to a human quality review.

**Layer 4 — Human review queue (production)**
- Escalated conversations and flagged responses are queued
- A human reviews 5-10% of production traffic weekly
- Findings feed back into the ground truth dataset

---

## 6. How This Maps to Arize's Products

When talking to the interviewer, connect your work explicitly to Arize's offerings:

| What You Built | Arize Product | Connection |
|----------------|---------------|------------|
| Phoenix spans for each guardrail step | Phoenix / Arize tracing | Same concept — every LLM call is a span with attributes |
| Multi-agent span hierarchy (orchestrator → agent → tool) | Phoenix multi-agent tracing | Phoenix natively renders agent hierarchies as trace trees |
| Cosine similarity evaluation | Arize embedding evaluation | Arize computes drift on embedding similarity over time |
| LLM-as-judge (accuracy, helpfulness, tone) | Arize LLM evaluators | Arize has built-in judge templates for these exact dimensions |
| Blocking accuracy quality gate | Arize monitors & alerts | Arize can alert on custom metric thresholds |
| Human review queue | Arize human annotation | Arize supports human annotation workflows for production traces |
| CI quality gate | Arize CI integration | Arize has a Python SDK for evaluation in CI pipelines |

**Key talking point:** "Phoenix is what I used here because it's open-source and
deployable locally without an account. For a production customer, the natural
upgrade is the Arize managed platform, which adds long-term data retention,
team collaboration, statistical drift detection, and built-in model comparison.
The instrumentation code is the same — you just change the endpoint."

---

## 7. Areas for Improvement — Show Engineering Maturity

Interviewers at observability companies will respect you for knowing the limits
of your own work.

**1. Model quality is the biggest gap.**
SmolLM2 135M produces robotic responses and frequently misses nuance. The right
next step is Llama 3.2 3B. For complex complaint handling, Mistral 7B.
The architecture already supports this — it's an env var change.

**2. The intent classifier has hard edges.**
Multi-intent queries ("check my order and tell me about your return policy") get
routed to only one agent. A production system needs a query decomposition step
or multi-agent fan-out.

**3. In-memory rate limiting doesn't scale horizontally.**
The current slowapi setup uses a process-local counter. Two replicas means two
independent counters — a user can double their effective rate limit by hitting
different instances. Fix: switch to Redis storage via `limits.storage.RedisStorage`.
The REDIS_URL config var is already wired in for exactly this.

**4. The evaluation dataset is synthetic.**
The 100-case dataset in `data/qa_dataset.json` was pre-written. A production
evaluation system should be built on real user queries sampled from Phoenix traces.
This is actually an Arize strength: Phoenix makes it easy to export production
traces as evaluation datasets.

**5. No embedding-based similarity guardrail.**
The current prompt injection detection is regex. A more robust layer would use
embedding similarity to detect queries that are semantically similar to known
injection patterns, even if they use novel phrasing. This is a case where Arize's
vector-based anomaly detection would add real value.

**6. No model versioning.**
When you upgrade from SmolLM2 to Llama 3.2, you cannot compare response quality
before and after on the same queries. Arize solves this with model versioning and
A/B comparison on production traffic.

---

## 8. Common Interview Questions with Prepared Answers

---

**Q: "Walk me through what happens when a customer sends a message."**

"The request hits nginx, which enforces rate limiting and security headers. It
goes to the FastAPI app where an API key check runs. Then the five-step guardrail
pipeline: input normalization, injection detection, policy violation check. If it
passes all three, the orchestrator takes over: the IntentClassifier runs a keyword
scan in about 2ms and produces an intent — say, `order_status` — plus any
extracted entities like `ORD-10001`. That routes to the OrderAgent, which calls
`get_order('ORD-10001')` deterministically, formats the order data as context,
injects it into the LLM prompt, and generates a response. The response goes
through output moderation — checking for system prompt leaks and sensitive data
patterns — and the final response includes the intent, agent name, and tools
called. Every span in that pipeline is traced through Phoenix."

---

**Q: "How do you evaluate whether the system is working well?"**

"Three automated layers and one human layer. In CI, a guardrail accuracy gate:
95% of injection and policy violation test cases must be correctly blocked.
A semantic similarity gate on the 'safe' queries: cosine distance between the
model's response and the ground truth answer. And an LLM-as-judge gate: Claude
Haiku scores each response on accuracy, helpfulness, tone, and completeness.
In production, escalated and flagged conversations go to a human review queue
where someone reviews 5-10% of traffic weekly. The findings from that review feed
back into the evaluation dataset."

---

**Q: "Why does this matter for Arize's customers?"**

"Arize customers are deploying LLMs to do real work — customer support, code
review, document analysis. The failure modes for LLMs are subtle: a guardrail
that gets bypassed with a Unicode character, a model that starts hallucinating
after a fine-tune, an agent that routes incorrectly for a specific phrasing. None
of these failures are visible without instrumentation. This project demonstrates
in code the exact workflow an Arize customer needs: trace every call, evaluate
quality automatically, alert on regressions. When I show a customer their Phoenix
dashboard for the first time and they can see which agent handled each query and
why the guardrail fired, that's when observability becomes tangible rather than
theoretical."

---

**Q: "What would you do differently if you were building this for a real customer?"**

"Three things. First, the evaluation dataset would be built from production traces
— real user queries — not synthetic data. Phoenix makes that easy. Second, I'd add
model versioning so you can compare response quality before and after a model
upgrade on the same set of queries. Third, the rate limiting would use a Redis
backend so it works correctly across multiple app instances. The current in-memory
limiter means horizontal scaling breaks rate limiting — a real security gap."

---

**Q: "What do you know about Arize AI specifically?"**

"Arize is the observability layer for production ML — monitoring for drift,
accuracy degradation, and data quality issues. Phoenix is the open-source,
self-hosted version I used here — it's focused on LLM tracing and evaluation.
The managed Arize platform adds long-term retention, statistical drift detection,
model comparison, and team collaboration on top of the same instrumentation.
The core insight Arize is built on is that LLMs in production need the same
telemetry discipline as traditional software — you wouldn't run a web service
without logs and metrics, and you shouldn't run an LLM without traces and
evaluation. That's exactly what this project demonstrates."

---

**Q: "Tell me about a time you had to debug something hard."**

"The rate limiter test failure was a good one. I had a test that sent 21 requests
and expected the 21st to return 429. It was passing in isolation but failing when
run with the full test suite. I traced it to test isolation: slowapi's in-memory
storage is keyed by the `testclient` IP, and earlier tests had consumed part of
the 20-request budget before my rate limiting test ran. The fix was straightforward
— reset the storage at the start of the test — but the diagnosis required
understanding how the rate limiter state is shared across the test session. The
broader lesson: any shared mutable state in a test environment will eventually
produce order-dependent failures."

---

## 9. Demo Script (10-minute version)

Use this if asked to walk through the code live.

**Minute 1-2: The MVP problem**
- Open `aiengineer-llm-python` (the original)
- Show `app/guardrails.py` — simple regex, no normalization
- Show the "will FAIL" test docstrings
- Say: "This is where I started. The README itself admits tests are failing."

**Minute 3-4: Stage 1 — Security**
- Switch to `aiengineer-llm-python-prod`
- Show `app/guardrails.py` — `normalize_text()`, the confusable table
- Run in terminal: `python -c "from app.guardrails import detect_prompt_injection; print(detect_prompt_injection('іgnore previous instructions'))"`
- Expected: `(True, 'Prompt injection attempt detected')`

**Minute 5-6: Stage 2-3 — API + Observability**
- Show `app/main.py` — health endpoint, rate limiting, structlog, metrics mount
- Show `app/metrics.py` — 5 instruments
- Say: "Every request is logged as structured JSON with request_id. Every
  blocking event increments BLOCK_REASON_COUNT so you can alert on it."

**Minute 7-8: Multi-agent system**
- Show `app/agents/orchestrator.py` — the routing logic
- Show `app/agents/intent_classifier.py` — keyword classification
- Show `app/tools/orders.py` — deterministic tool lookup
- Show `data/mock_orders.json` — the data it reads
- Say: "The model only generates text. Routing and tool execution are
  deterministic — this is what makes it reliable with a 135M model."

**Minute 9: Evaluation**
- Show `evaluation/llm_evaluator.py` — the judge model
- Run: `python evaluation/run_llm_eval.py` (uses heuristic if no API key)
- Show `.github/workflows/ci.yml` — the quality gate jobs

**Minute 10: The Phoenix trace**
- Show `app/agents/orchestrator.py` — the `tracer.start_as_current_span()` calls
- Point out: orchestrator span → intent_classification span → agent:X span
- Say: "With Phoenix running, you'd see this full hierarchy for every request.
  The intent, the agent that handled it, the tools called — all visible."

---

## 10. One-Page Cheat Sheet

Print this and keep it on your desk during the interview.

```
PROJECT:  Customer Service AI
          MVP → Production (6 stages) → Multi-Agent (2 stages)

STACK:    FastAPI + Ollama (SmolLM2 135M) + LangChain
          Phoenix (tracing) + Prometheus (metrics) + structlog (logs)
          pytest (testing) + GitHub Actions (CI/CD)
          nginx (reverse proxy) + Docker (containerization)

SECURITY: normalize_text(): NFKC + confusable table + zero-width strip
          22 injection patterns (re.IGNORECASE)
          API key auth + slowapi rate limiting (20 req/min)
          nginx: rate limit + security headers + TLS placeholder

AGENTS:   IntentClassifier (keywords + regex entities)
          OrderAgent → get_order() tool
          ReturnsAgent → check_return_eligibility() tool
          ProductAgent → search_products() tool
          EscalationAgent → always → ReviewQueue
          GeneralAgent (fallback)

STATE:    InMemorySessionStore (async, Redis-ready, TTL 24h, max 10 turns)
          InMemoryReviewQueue (async, admin endpoint)

EVAL:     Blocking accuracy ≥ 95% (CI gate)
          Cosine similarity ≥ 30% (CI gate)
          LLM-as-judge ≥ 60% (Claude Haiku: acc/help/tone/complete)
          Human review queue (5-10% production sampling)

TESTS:    68 tests, 0 failing, 0 warnings (unit + integration)
          tests/test_guardrails.py — 20 tests (guardrail functions)
          tests/test_app.py — 17 tests (API endpoints, auth, rate limit)
          tests/test_agents.py — 17 tests (agents, orchestrator, session)
          tests/test_tools.py — 14 tests (tool functions, no Ollama)

GIT LOG:  feat(multi-agent): orchestrator, agents, tools, session, queue
          feat(stage-6): nginx, multi-stage Docker, prod compose
          feat(stage-5): evaluation quality gate, integration tests
          feat(stage-4): GitHub Actions CI/CD pipeline
          feat(stage-3): structlog, Prometheus metrics
          feat(stage-2): auth, rate limiting, timeout, health endpoint
          feat(stage-1): Unicode normalization, guardrail hardening

ARIZE:    Phoenix → same spans, add cloud endpoint
          Arize managed → adds drift detection, model comparison, retention
          LLM-as-judge → Arize has built-in evaluator templates
          Human queue → Arize annotation workflow
```
