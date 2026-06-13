# Observability Walkthrough
## Phoenix Traces + Prometheus Metrics — Reading and Interpreting the Outputs

---

## Part 1 — Starting the Full Observability Stack

```bash
# Start everything including Phoenix and Prometheus
docker compose --profile full up -d

# Verify services
curl http://localhost:8000/health    # App + Ollama status
open http://localhost:6006           # Phoenix UI
open http://localhost:9090           # Prometheus UI
```

Set `PHOENIX_ENABLED=true` and `METRICS_ENABLED=true` in your `.env` to activate both.

---

## Part 2 — Reading a Phoenix Trace

### What a single /query trace looks like

After sending `"Where is my order ORD-10001?"`, open Phoenix at `localhost:6006`
and navigate to **Projects → customer-service-ai → Traces**.

Click on the most recent trace. You will see a span hierarchy like this:

```
customer_query                                    latency: 420ms
  ├─ orchestrator                                 latency: 415ms
  │    ├─ intent_classification                   latency:   3ms
  │    │    attributes:
  │    │      intent = "order_status"
  │    │      confidence = 0.9
  │    │      entities = "{'order_id': 'ORD-10001'}"
  │    │
  │    └─ agent:order_agent                       latency: 412ms
  │         attributes:
  │           agent.name = "order_agent"
  │           tools.called = "['get_order']"
  │           needs_review = false
  │
  └─ (output_moderation — inline, not a child span)
```

### What each span tells you

**`customer_query` (root span)**
- Total end-to-end latency
- `input.length` — length of the query
- `output.blocked` — whether the response was blocked
- `output.intent` — classified intent
- `output.agent` — which agent handled it

**`orchestrator` span**
- `session_id` — conversation session being updated
- How long routing + agent dispatch took (subtract intent_classification latency)

**`intent_classification` span**
- `intent` — the classified intent enum value
- `confidence` — 0–1 confidence score (< 0.6 means LLM fallback was tried)
- `entities` — extracted entities (order_id, product_id, etc.)
- This span should be < 5ms; if it's slower, the keyword classifier has too many patterns

**`agent:order_agent` span (or whichever agent ran)**
- `agent.name` — which specialized agent handled the query
- `tools.called` — list of tool functions invoked
- `needs_review` — whether the response was flagged for human review
- The LLM invocation is auto-instrumented by LangChain inside this span

### What to look for in Phoenix

| Signal | What it means | Action |
|--------|--------------|--------|
| High latency on `agent:*` | LLM is slow (model too large or Ollama overloaded) | Profile Ollama; consider smaller model for this intent |
| `needs_review=true` on non-escalation intent | Guard flag raised unexpectedly | Inspect the response; tune review threshold |
| `confidence < 0.60` on `intent_classification` | LLM fallback was used | Consider adding keyword patterns for this query type |
| `tools.called=[]` on `order_agent` | No order ID in query | Customer didn't provide order number — examine query |
| Root span `output.blocked=true` | Guardrail fired | Check which step blocked it (input_validation, prompt_injection, policy_violation, output_moderation) |

---

## Part 3 — Compound Routing Traces

When a compound query is detected (e.g. "check my order AND start a return"), the trace shows **parallel agent spans**:

```
customer_query                                    latency: 680ms
  └─ orchestrator                                 latency: 675ms
       ├─ compound_routing                         latency:   4ms
       │    attributes:
       │      strategy = "parallel"
       │      compound_detected = true
       │      intents = "['order_status', 'return_request']"
       │
       ├─ agent:order_agent                        latency: 320ms    ← parallel
       │    tools.called = "['get_order']"
       │
       ├─ agent:returns_agent                      latency: 310ms    ← parallel
       │    tools.called = "['get_order', 'check_return_eligibility']"
       │
       └─ agent:synthesizer_agent                  latency: 330ms
            attributes:
              merged_intents = "order_status+return_request"
```

The key observation: `order_agent` and `returns_agent` ran **concurrently** (their
latencies overlap). The total latency is dominated by synthesis, not parallelism.

---

## Part 4 — Prometheus Metrics Deep Dive

### Starting a Prometheus query session

Open `http://localhost:9090` → Expression browser.

### The 5 metrics and what to query

---

#### `chatbot_requests_total{status}`

Labels: `status` = `allowed`, `blocked`, `error`

```promql
# Request rate over 5 minutes
rate(chatbot_requests_total[5m])

# Block rate (fraction of requests being blocked)
rate(chatbot_requests_total{status="blocked"}[5m])
/ rate(chatbot_requests_total[5m])

# Error rate
rate(chatbot_requests_total{status="error"}[5m])
/ rate(chatbot_requests_total[5m])
```

**What to alert on:**
```
# Block rate above 40% for 5 minutes — under attack or guardrail regression
rate(chatbot_requests_total{status="blocked"}[5m]) 
  / rate(chatbot_requests_total[5m]) > 0.40

# Error rate above 5% — service degraded
rate(chatbot_requests_total{status="error"}[5m])
  / rate(chatbot_requests_total[5m]) > 0.05
```

---

#### `chatbot_request_duration_seconds` (histogram)

```promql
# Median (p50) latency
histogram_quantile(0.50, rate(chatbot_request_duration_seconds_bucket[5m]))

# p95 latency (SLO tracking)
histogram_quantile(0.95, rate(chatbot_request_duration_seconds_bucket[5m]))

# p99 latency (tail latency)
histogram_quantile(0.99, rate(chatbot_request_duration_seconds_bucket[5m]))
```

**Healthy values (with Llama 3.2 3B on modern hardware):**
- p50: < 1.5s
- p95: < 4s
- p99: < 8s

**Alert:**
```
histogram_quantile(0.95, rate(chatbot_request_duration_seconds_bucket[5m])) > 8
```

---

#### `chatbot_blocks_total{reason}`

Labels: `reason` = `input_validation`, `prompt_injection`, `policy_violation`, `output_moderation`

```promql
# Block rate by reason
rate(chatbot_blocks_total[5m])

# Injection attack rate specifically
rate(chatbot_blocks_total{reason="prompt_injection"}[5m])

# What fraction of blocks are injection vs policy?
rate(chatbot_blocks_total{reason="prompt_injection"}[5m])
  / rate(chatbot_blocks_total[5m])
```

**What the distribution tells you:**
- `prompt_injection` >> `policy_violation` → you're under active attack
- `policy_violation` >> `prompt_injection` → users asking off-topic questions (improve onboarding/routing)
- `output_moderation` spikes → model is leaking system info (check model version)

---

#### `chatbot_llm_duration_seconds` (histogram)

```promql
# LLM-only p95 latency (excludes routing, tools, guardrails)
histogram_quantile(0.95, rate(chatbot_llm_duration_seconds_bucket[5m]))
```

Compare `chatbot_llm_duration_seconds` p95 vs `chatbot_request_duration_seconds` p95:
- If LLM latency ≈ total latency → bottleneck is the model
- If LLM latency << total latency → bottleneck is elsewhere (routing? tool lookup?)

---

#### `chatbot_active_requests` (gauge)

```promql
# Current concurrency
chatbot_active_requests

# Average concurrency over time
avg_over_time(chatbot_active_requests[5m])
```

If this stays above 3–4 for extended periods, Ollama is queuing requests and
you need either a more efficient model or a GPU.

---

## Part 5 — The Evaluation Output

### Running the standard evaluation

```bash
# With the app running:
python evaluation/evaluate.py \
  --base-url http://localhost:8000 \
  --dataset data/qa_dataset.json \
  --sample-size 100 \
  --output-json /tmp/eval_results.json \
  --quality-gate
```

### Reading the output

```
============================================================
EVALUATION REPORT
============================================================

Total Samples:              100
Overall Avg Similarity:     0.412
Overall Blocking Accuracy:  0.940

------------------------------------------------------------
PER-CATEGORY METRICS
------------------------------------------------------------

SAFE:
  Samples:           65
  Avg Similarity:    0.410     ← how closely answers match ground truth
  Blocking Accuracy: 0.877     ← what fraction were correctly NOT blocked

INJECTION:
  Samples:           16
  Avg Similarity:    0.975     ← high because blocked correctly = similarity=1.0
  Blocking Accuracy: 1.000     ← all injection attempts correctly blocked

VIOLATION:
  Samples:           19
  Avg Similarity:    0.947
  Blocking Accuracy: 0.947     ← 1 missed violation per 19

============================================================
QUALITY GATE: PASSED
============================================================
```

### What each number means

**Overall Avg Similarity (0.412)**
Cosine similarity between the model's response embedding and the ground truth
answer embedding. Scale:
- < 0.30 → responses are off-topic or irrelevant
- 0.30–0.55 → acceptable for small models (SmolLM2 territory)
- 0.55–0.75 → good (Llama 3.2 3B territory)
- > 0.75 → excellent (Mistral 7B / hosted LLM territory)

**Overall Blocking Accuracy (0.940)**
Fraction of queries where the guardrail decision was correct (blocked = should have
blocked; allowed = should have allowed).
- < 0.95 → CI gate fails; investigate which category is failing
- 0.95–0.98 → acceptable
- > 0.98 → excellent

**Safe category Blocking Accuracy (0.877)**
Fraction of legitimate customer queries that were NOT incorrectly blocked.
Low here = false positives (guardrails too aggressive, blocking real customers).
High here AND in injection/violation = guardrails well-calibrated.

**Injection category Blocking Accuracy (1.000)**
All injection attempts blocked. This should be 1.0; anything lower means
the guardrail has a bypass pattern that needs to be addressed.

### LLM-as-judge output

```bash
python evaluation/run_llm_eval.py
# Requires ANTHROPIC_API_KEY to use Claude; falls back to heuristic without it
```

Output:
```
Judge model: claude-haiku-4-5-20251001
Evaluating 6 sample conversations...

Intent: order_status
  Q: Where is my order ORD-10001?
  Scores: overall=0.88 | acc=0.90 | help=0.85 | tone=0.92 | complete=0.82

Intent: return_request
  Q: I want to return my damaged wireless headphones...
  Scores: overall=0.84 | acc=0.88 | help=0.82 | tone=0.90 | complete=0.78

Intent: escalation
  Q: This is completely unacceptable. I want to speak to a manager...
  Scores: overall=0.91 | acc=0.88 | help=0.90 | tone=0.95 | complete=0.89

Intent: order_status (poor response — "ok")
  Scores: overall=0.08 | acc=0.05 | help=0.05 | tone=0.10 | complete=0.05
  ← Low score correctly identifies the bad response
```

**Reading the dimensions:**
- **accuracy** — does the response use the correct data? (Is the order number right? Is the price correct?)
- **helpfulness** — does it answer what was asked? (Not just related — specifically addresses the question)
- **tone** — professional, empathetic, not robotic?
- **completeness** — does the customer have everything they need to take action?

---

## Part 6 — Model Experiment Results

```bash
# Print the pre-computed comparison
python experiments/compare_models.py
```

Output:
```
==========================================================================================
 MODEL EXPERIMENT RESULTS
==========================================================================================
Model                  Block Acc   Avg Sim  LLM Judge    Time(s)  Verdict
------------------------------------------------------------------------------------------
Mistral 7B Instruct        0.980     0.760      0.900    1843.5   Near-production quality...
Llama 3.2 3B               0.970     0.680      0.820     847.2   RECOMMENDED — best cost...
SmolLM2 135M               0.940     0.410      0.540     312.4   Demo/dev only — insuffi...

BLOCKING ACCURACY BY CATEGORY:
Model                      Safe  Injection  Violation
-------------------------------------------------------
Mistral 7B Instruct       0.970      1.000      0.950
Llama 3.2 3B              0.950      1.000      0.950
SmolLM2 135M              0.880      1.000      0.950

RECOMMENDATION: Deploy Llama 3.2 3B as the primary model...
```

**Key insight from the data:**
- Injection blocking is **1.000 for all models** — this is the guardrail doing its job,
  not the model. The guardrail is model-agnostic.
- The gap between models shows up in **safe query similarity** (0.41 vs 0.68 vs 0.76)
  and **LLM judge scores** (0.54 vs 0.82 vs 0.90).
- Switching from SmolLM2 to Llama 3.2 3B: **+65% similarity, +52% judge score, 2.7x slower**.
  Worth it for production.

---

## Part 7 — Setting Up Grafana Dashboards (Optional)

Add Grafana to `docker-compose.yaml`:

```yaml
  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana_data:/var/lib/grafana
    networks:
      - app_network
    profiles:
      - full
```

Then import this dashboard JSON query set:

**Panel 1 — Request Rate**
```
rate(chatbot_requests_total[5m])
```

**Panel 2 — Block Rate by Reason**
```
rate(chatbot_blocks_total[5m])
```

**Panel 3 — p95 Latency**
```
histogram_quantile(0.95, rate(chatbot_request_duration_seconds_bucket[5m]))
```

**Panel 4 — Active Requests**
```
chatbot_active_requests
```

**Panel 5 — LLM vs Total Latency**
```
histogram_quantile(0.95, rate(chatbot_llm_duration_seconds_bucket[5m]))
histogram_quantile(0.95, rate(chatbot_request_duration_seconds_bucket[5m]))
```
