"""FastAPI application for customer service with multi-agent LLM integration.

Production features:
- Multi-agent orchestration: intent classification → specialized agent → tool augmentation
- API key authentication (disabled when API_KEY env var is empty)
- Per-IP rate limiting via slowapi
- LLM call timeout with asyncio.wait_for + asyncio.to_thread
- Prometheus metrics at /metrics
- Structured JSON logging via structlog
- Health check endpoint with live Ollama connectivity test
- Conversation session management (in-memory, Redis-ready)
- Human review queue for flagged/escalated conversations
- Phoenix / OpenTelemetry tracing (optional)
"""

import asyncio
import time
import uuid

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from langchain_ollama import OllamaLLM
from prometheus_client import make_asgi_app
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import (
    APP_ENV,
    LLM_MODEL,
    LLM_TIMEOUT_SECONDS,
    METRICS_ENABLED,
    OLLAMA_BASE_URL,
    PHOENIX_ENABLED,
    PHOENIX_ENDPOINT,
    RATE_LIMIT_PER_MINUTE,
    REVIEW_QUEUE_ENABLED,
    SYSTEM_PROMPT,
)
from app.guardrails import (
    detect_policy_violation,
    detect_prompt_injection,
    filter_input,
    moderate_output,
)
from app.metrics import (
    ACTIVE_REQUESTS,
    BLOCK_REASON_COUNT,
    LLM_LATENCY,
    REQUEST_COUNT,
    REQUEST_LATENCY,
)
from app.middleware.auth import verify_api_key
from app.agents.orchestrator import CustomerServiceOrchestrator
from app.state.session import get_session_store
from app.queue.review_queue import get_review_queue

# ---------------------------------------------------------------------------
# Structured logging setup
# ---------------------------------------------------------------------------
_shared_processors = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
]

if APP_ENV == "production":
    structlog.configure(
        processors=[
            *_shared_processors,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
else:
    structlog.configure(
        processors=[
            *_shared_processors,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(10),  # DEBUG
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Phoenix tracing (optional)
# ---------------------------------------------------------------------------
tracer = None
_phoenix_enabled = PHOENIX_ENABLED

if _phoenix_enabled:
    try:
        from phoenix.otel import register
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from opentelemetry import trace as otel_trace

        tracer_provider = register(
            project_name="customer-service-ai",
            endpoint=f"{PHOENIX_ENDPOINT}/v1/traces",
        )
        LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
        tracer = otel_trace.get_tracer(__name__)
        logger.info("phoenix_tracing_enabled", endpoint=PHOENIX_ENDPOINT)
    except Exception as exc:
        logger.warning("phoenix_tracing_failed", error=str(exc))
        _phoenix_enabled = False
        tracer = None
else:
    logger.info("phoenix_tracing_disabled")

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Customer Service AI",
    description="E-commerce customer service with multi-agent AI orchestration",
    version="2.0.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

if METRICS_ENABLED:
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)
    logger.info("prometheus_metrics_enabled", path="/metrics")

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
llm = OllamaLLM(
    base_url=OLLAMA_BASE_URL,
    model=LLM_MODEL,
    temperature=0.7,
    max_tokens=200,
)

# ---------------------------------------------------------------------------
# Multi-agent system singletons
# ---------------------------------------------------------------------------
_session_store = get_session_store()
_review_queue = get_review_queue()
_orchestrator = CustomerServiceOrchestrator(
    llm=llm,
    session_store=_session_store,
    review_queue=_review_queue,
    tracer=tracer,
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class QueryRequest(BaseModel):
    question: str
    session_id: str | None = None


class QueryResponse(BaseModel):
    answer: str
    blocked: bool = False
    reason: str = ""
    request_id: str = ""
    intent: str = ""
    agent_name: str = ""
    tools_called: list[str] = []
    needs_review: bool = False
    session_id: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def root() -> dict:
    return {
        "status": "ok",
        "service": "Customer Service AI",
        "version": "2.0.0",
        "env": APP_ENV,
    }


@app.get("/health")
async def health() -> dict:
    """Health check with live Ollama connectivity probe."""
    ollama_status = "down"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                ollama_status = "up"
    except Exception as exc:
        logger.warning("health_check_ollama_unreachable", error=str(exc))

    session_count = await _session_store.session_count()
    queue_size = await _review_queue.size()

    return {
        "status": "ok" if ollama_status == "up" else "degraded",
        "ollama": ollama_status,
        "model": LLM_MODEL,
        "active_sessions": session_count,
        "review_queue_depth": queue_size,
    }


@app.get("/sessions/{session_id}")
async def get_session(session_id: str, _auth: bool = Depends(verify_api_key)) -> dict:
    """Return conversation history for a session."""
    session = await _session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "turns": len(session.turns),
        "history": [{"user": t.user, "assistant": t.assistant, "intent": t.intent} for t in session.turns],
        "created_at": session.created_at,
    }


@app.get("/admin/review-queue")
async def get_review_queue_items(_auth: bool = Depends(verify_api_key)) -> dict:
    """Return pending review queue items (admin endpoint)."""
    items = await _review_queue.list_pending(limit=50)
    return {"queue_depth": await _review_queue.size(), "items": items}


@app.post("/query", response_model=QueryResponse)
@limiter.limit(f"{RATE_LIMIT_PER_MINUTE}/minute")
async def query(
    request: Request,
    body: QueryRequest,
    _auth: bool = Depends(verify_api_key),
) -> QueryResponse:
    """Process a customer service query through the guardrail + multi-agent pipeline."""
    request_id = str(uuid.uuid4())
    session_id = body.session_id or str(uuid.uuid4())
    start_time = time.perf_counter()

    ACTIVE_REQUESTS.inc()
    log = logger.bind(request_id=request_id, session_id=session_id)
    log.info("request_received", question_length=len(body.question))

    try:
        if _phoenix_enabled and tracer:
            with tracer.start_as_current_span("customer_query") as span:
                span.set_attribute("input.value", body.question)
                span.set_attribute("input.length", len(body.question))
                span.set_attribute("request_id", request_id)
                span.set_attribute("session_id", session_id)
                response = await _process_query(body.question, session_id, request_id, span, log)
        else:
            response = await _process_query(body.question, session_id, request_id, None, log)

        response.request_id = request_id
        response.session_id = session_id

        elapsed = time.perf_counter() - start_time
        REQUEST_LATENCY.observe(elapsed)
        status_label = "blocked" if response.blocked else "allowed"
        REQUEST_COUNT.labels(status=status_label).inc()

        log.info(
            "request_completed",
            blocked=response.blocked,
            intent=response.intent,
            agent=response.agent_name,
            tools_called=response.tools_called,
            needs_review=response.needs_review,
            latency_seconds=round(elapsed, 4),
        )

        return response

    except HTTPException:
        elapsed = time.perf_counter() - start_time
        REQUEST_COUNT.labels(status="error").inc()
        REQUEST_LATENCY.observe(elapsed)
        raise
    except Exception as exc:
        elapsed = time.perf_counter() - start_time
        REQUEST_COUNT.labels(status="error").inc()
        REQUEST_LATENCY.observe(elapsed)
        log.error("request_unhandled_error", error=str(exc))
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    finally:
        ACTIVE_REQUESTS.dec()


# ---------------------------------------------------------------------------
# Internal pipeline
# ---------------------------------------------------------------------------

async def _process_query(
    question: str,
    session_id: str,
    request_id: str,
    parent_span,
    log,
) -> QueryResponse:
    """5-step guardrail pipeline + multi-agent orchestration."""

    def _span_attr(span, key, value):
        if span is not None:
            span.set_attribute(key, value)

    # Step 1: Input filtering
    is_valid, filtered_or_reason = filter_input(question)
    if not is_valid:
        log.warning("input_filtered", reason=filtered_or_reason)
        _span_attr(parent_span, "output.blocked", True)
        _span_attr(parent_span, "output.block_reason", "input_validation")
        BLOCK_REASON_COUNT.labels(reason="input_validation").inc()
        return QueryResponse(
            answer="",
            blocked=True,
            reason=f"Input validation failed: {filtered_or_reason}",
        )

    filtered_question = filtered_or_reason

    # Step 2: Prompt injection detection
    is_injection, injection_reason = detect_prompt_injection(filtered_question)
    if is_injection:
        log.warning("prompt_injection_detected", reason=injection_reason)
        _span_attr(parent_span, "output.blocked", True)
        _span_attr(parent_span, "output.block_reason", "prompt_injection")
        BLOCK_REASON_COUNT.labels(reason="prompt_injection").inc()
        return QueryResponse(
            answer="",
            blocked=True,
            reason="I cannot process this request. Reason: Prompt injection attempt detected.",
        )

    # Step 3: Policy violation detection
    is_violation, violation_reason = detect_policy_violation(filtered_question)
    if is_violation:
        log.warning("policy_violation_detected", reason=violation_reason)
        _span_attr(parent_span, "output.blocked", True)
        _span_attr(parent_span, "output.block_reason", "policy_violation")
        BLOCK_REASON_COUNT.labels(reason="policy_violation").inc()
        return QueryResponse(
            answer="",
            blocked=True,
            reason=f"I cannot process this request. Reason: {violation_reason}",
        )

    # Step 4: Multi-agent orchestration (intent → route → tool → LLM)
    llm_start = time.perf_counter()
    try:
        orch_result = await _orchestrator.process(
            query=filtered_question,
            session_id=session_id,
            request_id=request_id,
        )
        response_text = orch_result.response
    except (TimeoutError, asyncio.TimeoutError):
        llm_elapsed = time.perf_counter() - llm_start
        log.error("llm_timeout", timeout_seconds=LLM_TIMEOUT_SECONDS, elapsed=llm_elapsed)
        raise HTTPException(
            status_code=504,
            detail=f"LLM did not respond within {LLM_TIMEOUT_SECONDS} seconds. Please try again.",
        )
    except httpx.ConnectError as exc:
        log.error("llm_connection_error", error=str(exc))
        raise HTTPException(
            status_code=503,
            detail="LLM service is currently unavailable. Please try again later.",
        )
    except Exception as exc:
        log.error("llm_invocation_error", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Failed to generate response. Please try again later.",
        )
    finally:
        LLM_LATENCY.observe(time.perf_counter() - llm_start)

    # Step 5: Output moderation
    is_safe, safety_reason = moderate_output(response_text)
    if not is_safe:
        log.warning("output_moderation_blocked", reason=safety_reason)
        _span_attr(parent_span, "output.blocked", True)
        _span_attr(parent_span, "output.block_reason", "output_moderation")
        BLOCK_REASON_COUNT.labels(reason="output_moderation").inc()
        return QueryResponse(
            answer="",
            blocked=True,
            reason="I apologize, but I cannot provide this response due to safety concerns.",
            intent=orch_result.intent,
            agent_name=orch_result.agent_name,
        )

    _span_attr(parent_span, "output.blocked", False)
    _span_attr(parent_span, "output.intent", orch_result.intent)
    _span_attr(parent_span, "output.agent", orch_result.agent_name)
    _span_attr(parent_span, "output.tools_called", str(orch_result.tools_called))

    return QueryResponse(
        answer=response_text,
        blocked=False,
        reason="",
        intent=orch_result.intent,
        agent_name=orch_result.agent_name,
        tools_called=orch_result.tools_called,
        needs_review=orch_result.needs_review,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
