"""CustomerServiceOrchestrator — routes queries to specialized agents with full Phoenix tracing."""
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

from app.agents.base import AgentResult
from app.agents.intent_classifier import IntentClassifier, Intent
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

class CustomerServiceOrchestrator:
    def __init__(self, llm, session_store, review_queue, tracer=None):
        self.classifier = IntentClassifier()
        self.session_store = session_store
        self.review_queue = review_queue
        self.tracer = tracer
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

        # Step 2: Classify intent + extract entities
        if self.tracer and parent_span:
            with self.tracer.start_as_current_span("intent_classification") as span:
                classification = self.classifier.classify(query)
                span.set_attribute("intent", classification.intent.value)
                span.set_attribute("confidence", classification.confidence)
                span.set_attribute("entities", str(classification.entities))
        else:
            classification = self.classifier.classify(query)

        log.info(
            "intent_classified",
            intent=classification.intent.value,
            confidence=classification.confidence,
            entities=classification.entities,
        )

        # Step 3: Route to agent
        agent = self.agents[classification.intent]
        if self.tracer and parent_span:
            with self.tracer.start_as_current_span(f"agent:{agent.name}") as span:
                span.set_attribute("agent.name", agent.name)
                span.set_attribute("tools.available", str(list(vars(agent).keys())))
                agent_result: AgentResult = await agent.process(
                    query, classification.entities, history
                )
                span.set_attribute("tools.called", str(agent_result.tools_called))
                span.set_attribute("needs_review", agent_result.needs_review)
        else:
            agent_result = await agent.process(query, classification.entities, history)

        log.info(
            "agent_completed",
            agent=agent.name,
            tools_called=agent_result.tools_called,
            needs_review=agent_result.needs_review,
        )

        # Step 4: Update conversation session
        await self.session_store.add_turn(session_id, query, agent_result.response)

        # Step 5: Route to review queue if needed
        if agent_result.needs_review:
            await self.review_queue.enqueue({
                "session_id": session_id,
                "request_id": request_id,
                "intent": classification.intent.value,
                "query": query,
                "response": agent_result.response,
                "agent": agent.name,
                "tools_called": agent_result.tools_called,
            })
            log.info("review_queue_enqueued", session_id=session_id)

        return OrchestratorResult(
            response=agent_result.response,
            intent=classification.intent.value,
            agent_name=agent.name,
            session_id=session_id,
            request_id=request_id,
            tools_called=agent_result.tools_called,
            tool_results=agent_result.tool_results,
            needs_review=agent_result.needs_review,
            classification_confidence=classification.confidence,
        )
