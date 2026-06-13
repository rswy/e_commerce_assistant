"""CustomerServiceOrchestrator — routes queries to specialized agents with full Phoenix tracing."""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

from app.agents.base import AgentResult
from app.agents.intent_classifier import IntentClassifier, Intent
from app.agents.compound_router import CompoundRouter
from app.agents.synthesizer_agent import SynthesizerAgent
from app.agents.order_agent import OrderAgent
from app.agents.returns_agent import ReturnsAgent
from app.agents.product_agent import ProductAgent
from app.agents.escalation_agent import EscalationAgent
from app.agents.general_agent import GeneralAgent

logger = structlog.get_logger(__name__)

@dataclass
class OrchestratorResult:
    response: str
    intent: str
    agent_name: str
    session_id: str
    request_id: str
    tools_called: list[str] = field(default_factory=list)
    tool_results: dict[str, Any] = field(default_factory=dict)
    needs_review: bool = False
    latency_ms: float = 0.0
    classification_confidence: float = 0.0
    routing_strategy: str = "single"
    compound_detected: bool = False

class CustomerServiceOrchestrator:
    def __init__(self, llm, session_store, review_queue, tracer=None):
        self._classifier = IntentClassifier()
        self._router = CompoundRouter(llm=llm)
        self._synthesizer = SynthesizerAgent(llm=llm, tracer=tracer)
        self.session_store = session_store
        self.review_queue = review_queue
        self.tracer = tracer
        # Keep backward-compatible attribute for any callers using orchestrator.classifier
        self.classifier = self._classifier
        # Initialize all agents
        self.agents = {
            Intent.ORDER_STATUS:     OrderAgent(llm, tracer),
            Intent.RETURN_REQUEST:   ReturnsAgent(llm, tracer),
            Intent.PRODUCT_QUESTION: ProductAgent(llm, tracer),
            Intent.ESCALATION:       EscalationAgent(llm, tracer),
            Intent.ACCOUNT_ISSUE:    GeneralAgent(llm, tracer),
            Intent.GENERAL:          GeneralAgent(llm, tracer),
        }

    async def process(
        self,
        query: str,
        session_id: str,
        request_id: str,
    ) -> OrchestratorResult:
        start = time.perf_counter()
        log = logger.bind(request_id=request_id, session_id=session_id)

        if self.tracer:
            with self.tracer.start_as_current_span("orchestrator") as span:
                span.set_attribute("session_id", session_id)
                span.set_attribute("request_id", request_id)
                result = await self._process_internal(query, session_id, request_id, log, span)
        else:
            result = await self._process_internal(query, session_id, request_id, log, None)

        result.latency_ms = (time.perf_counter() - start) * 1000
        return result

    async def _process_internal(self, query, session_id, request_id, log, parent_span) -> OrchestratorResult:
        # Step 1: Get conversation history
        history = await self.session_store.get_history(session_id)
        log.debug("session_history_loaded", turns=len(history))

        # Step 2: Compound routing decision (replaces single-intent classify)
        if self.tracer and parent_span:
            with self.tracer.start_as_current_span("compound_routing") as span:
                decision = await self._router.route_with_llm_fallback(query)
                span.set_attribute("routing.strategy", decision.strategy)
                span.set_attribute("routing.compound_detected", decision.compound_detected)
                span.set_attribute("routing.intents", str([r[0].value for r in decision.routes]))
                span.set_attribute("routing.confidence", decision.classification_confidence)
        else:
            decision = await self._router.route_with_llm_fallback(query)

        log.info(
            "routing_decision",
            strategy=decision.strategy,
            compound_detected=decision.compound_detected,
            intents=[r[0].value for r in decision.routes],
            confidence=decision.classification_confidence,
        )

        # Step 3: Dispatch — parallel for compound, single for everything else
        if decision.is_compound:
            agent_result, used_intent = await self._dispatch_parallel(
                query, decision, history, log, parent_span
            )
        else:
            agent_result, used_intent = await self._dispatch_single(
                query, decision, history, log, parent_span
            )

        log.info(
            "agent_completed",
            agent=agent_result.agent_name,
            tools_called=agent_result.tools_called,
            needs_review=agent_result.needs_review,
            strategy=decision.strategy,
            compound_detected=decision.compound_detected,
        )

        # Step 4: Update conversation session
        await self.session_store.add_turn(session_id, query, agent_result.response)

        # Step 5: Route to review queue if needed
        if agent_result.needs_review:
            await self.review_queue.enqueue({
                "session_id": session_id,
                "request_id": request_id,
                "intent": used_intent,
                "query": query,
                "response": agent_result.response,
                "agent": agent_result.agent_name,
                "tools_called": agent_result.tools_called,
            })
            log.info("review_queue_enqueued", session_id=session_id)

        return OrchestratorResult(
            response=agent_result.response,
            intent=used_intent,
            agent_name=agent_result.agent_name,
            session_id=session_id,
            request_id=request_id,
            tools_called=agent_result.tools_called,
            tool_results=agent_result.tool_results,
            needs_review=agent_result.needs_review,
            classification_confidence=decision.classification_confidence,
            routing_strategy=decision.strategy,
            compound_detected=decision.compound_detected,
        )

    async def _dispatch_single(
        self,
        query: str,
        decision,
        history: list[dict],
        log,
        parent_span,
    ) -> tuple[AgentResult, str]:
        """Dispatch to a single agent (existing logic, now routing-aware)."""
        intent, sub_query = decision.routes[0]
        entities = self._classifier.extract_entities(sub_query)
        agent = self.agents.get(intent, self.agents[Intent.GENERAL])

        if self.tracer and parent_span:
            with self.tracer.start_as_current_span(f"agent:{agent.name}") as span:
                span.set_attribute("agent.name", agent.name)
                span.set_attribute("tools.available", str(list(vars(agent).keys())))
                agent_result: AgentResult = await agent.process(sub_query, entities, history)
                span.set_attribute("tools.called", str(agent_result.tools_called))
                span.set_attribute("needs_review", agent_result.needs_review)
        else:
            agent_result = await agent.process(sub_query, entities, history)

        return agent_result, intent.value

    async def _dispatch_parallel(
        self,
        query: str,
        decision,
        history: list[dict],
        log,
        parent_span,
    ) -> tuple[AgentResult, str]:
        """Dispatch to multiple agents concurrently, then synthesize."""
        log.info(
            "parallel_dispatch_start",
            intents=[r[0].value for r in decision.routes],
            route_count=len(decision.routes),
        )

        async def _run_agent(intent: Intent, sub_query: str) -> AgentResult:
            entities = self._classifier.extract_entities(sub_query)
            agent = self.agents.get(intent, self.agents[Intent.GENERAL])

            if self.tracer and parent_span:
                with self.tracer.start_as_current_span(f"agent:{agent.name}") as span:
                    span.set_attribute("agent.name", agent.name)
                    span.set_attribute("routing.sub_query", sub_query)
                    result = await agent.process(sub_query, entities, history)
                    span.set_attribute("tools.called", str(result.tools_called))
                    span.set_attribute("needs_review", result.needs_review)
                    return result
            else:
                return await agent.process(sub_query, entities, history)

        # Run all agents concurrently
        agent_results = list(await asyncio.gather(
            *[_run_agent(intent, sub_query) for intent, sub_query in decision.routes]
        ))

        # Synthesize into one coherent response
        if self.tracer and parent_span:
            with self.tracer.start_as_current_span("synthesizer") as span:
                span.set_attribute("agent.count", len(agent_results))
                synthesized = await self._synthesizer.process(query, agent_results, history)
                span.set_attribute("needs_review", synthesized.needs_review)
        else:
            synthesized = await self._synthesizer.process(query, agent_results, history)

        used_intent = "+".join(r.intent for r in agent_results)
        return synthesized, used_intent
