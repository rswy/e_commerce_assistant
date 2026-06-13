"""Unit tests for the multi-agent system.

All LLM calls are mocked so no Ollama instance is required.
"""
import asyncio
import datetime
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from app.agents.intent_classifier import IntentClassifier, Intent
from app.agents.base import BaseAgent, AgentResult
from app.agents.order_agent import OrderAgent
from app.agents.returns_agent import ReturnsAgent
from app.agents.escalation_agent import EscalationAgent
from app.agents.orchestrator import CustomerServiceOrchestrator
from app.state.session import InMemorySessionStore
from app.queue.review_queue import InMemoryReviewQueue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mock_llm(response: str = "Test response from mock LLM.") -> MagicMock:
    """Return a mock LLM whose .invoke() returns the given string."""
    llm = MagicMock()
    llm.invoke.return_value = response
    return llm


def make_order(
    order_id: str = "ORD-10001",
    status: str = "shipped",
    days_ago: int = 5,
) -> dict:
    """Create a minimal order dict for testing."""
    placed = (datetime.date.today() - datetime.timedelta(days=days_ago)).isoformat()
    est = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    return {
        "order_id": order_id,
        "customer_id": "CUST-001",
        "status": status,
        "placed_date": placed,
        "est_delivery": est,
        "tracking_number": "TRACK123",
        "items": [{"name": "Widget", "product_id": "P001", "qty": 1, "unit_price_usd": 9.99}],
        "total_usd": 9.99,
        "shipping_address": "1 Test St, Testville, TX 00000",
    }


# ---------------------------------------------------------------------------
# IntentClassifier tests
# ---------------------------------------------------------------------------

class TestIntentClassifier:
    def setup_method(self):
        self.clf = IntentClassifier()

    def test_intent_classifier_order_status(self):
        result = self.clf.classify("where is my order ORD-10001")
        assert result.intent == Intent.ORDER_STATUS

    def test_intent_classifier_return(self):
        result = self.clf.classify("I want to return my damaged headphones")
        assert result.intent == Intent.RETURN_REQUEST

    def test_intent_classifier_product(self):
        result = self.clf.classify("how much do the wireless headphones cost")
        assert result.intent == Intent.PRODUCT_QUESTION

    def test_intent_classifier_escalation(self):
        result = self.clf.classify("I want to speak to a manager")
        assert result.intent == Intent.ESCALATION

    def test_intent_classifier_entity_extraction(self):
        result = self.clf.classify("My order ORD-10001 has not arrived yet")
        assert "order_id" in result.entities
        assert result.entities["order_id"] == "ORD-10001"


# ---------------------------------------------------------------------------
# OrderAgent tests
# ---------------------------------------------------------------------------

class TestOrderAgent:
    @pytest.mark.asyncio
    async def test_order_agent_with_existing_order(self):
        """OrderAgent calls get_order when order_id is present and order exists."""
        llm = make_mock_llm("Your order ORD-10001 has been shipped.")
        agent = OrderAgent(llm)
        order = make_order("ORD-10001", days_ago=5)

        with patch("app.agents.order_agent.get_order", return_value=order) as mock_get:
            result = await agent.process(
                query="Where is my order ORD-10001?",
                entities={"order_id": "ORD-10001"},
                history=[],
            )

        mock_get.assert_called_once_with("ORD-10001")
        assert "get_order" in result.tools_called
        assert result.agent_name == "order_agent"
        assert result.intent == "order_status"
        assert result.tool_results["order_found"] is True

    @pytest.mark.asyncio
    async def test_order_agent_with_missing_order(self):
        """OrderAgent handles missing orders gracefully."""
        llm = make_mock_llm("I could not find that order. Please verify the order number.")
        agent = OrderAgent(llm)

        with patch("app.agents.order_agent.get_order", return_value=None):
            result = await agent.process(
                query="Where is my order ORD-99999?",
                entities={"order_id": "ORD-99999"},
                history=[],
            )

        assert "get_order" in result.tools_called
        assert result.tool_results["order_found"] is False
        assert result.agent_name == "order_agent"


# ---------------------------------------------------------------------------
# ReturnsAgent tests
# ---------------------------------------------------------------------------

class TestReturnsAgent:
    @pytest.mark.asyncio
    async def test_returns_agent_eligible_order(self):
        """ReturnsAgent sets needs_review=False for eligible (recent) orders."""
        llm = make_mock_llm("Your order is eligible for return. Please visit returns.example.com.")
        agent = ReturnsAgent(llm)
        # Order placed 5 days ago — well within 30-day window
        order = make_order("ORD-10001", status="delivered", days_ago=5)

        with patch("app.agents.returns_agent.get_order", return_value=order):
            result = await agent.process(
                query="I want to return my order ORD-10001",
                entities={"order_id": "ORD-10001"},
                history=[],
            )

        assert result.needs_review is False
        assert "check_return_eligibility" in result.tools_called

    @pytest.mark.asyncio
    async def test_returns_agent_ineligible_order(self):
        """ReturnsAgent detects ineligibility for orders older than 30 days."""
        llm = make_mock_llm("Unfortunately this order is outside the return window.")
        agent = ReturnsAgent(llm)
        # Order placed 45 days ago — outside 30-day window
        order = make_order("ORD-10003", status="delivered", days_ago=45)

        with patch("app.agents.returns_agent.get_order", return_value=order):
            result = await agent.process(
                query="I want to return my order ORD-10003",
                entities={"order_id": "ORD-10003"},
                history=[],
            )

        eligibility = result.tool_results.get("eligibility", {})
        assert eligibility.get("eligible") is False
        # needs_review=True when not eligible
        assert result.needs_review is True


# ---------------------------------------------------------------------------
# EscalationAgent tests
# ---------------------------------------------------------------------------

class TestEscalationAgent:
    @pytest.mark.asyncio
    async def test_escalation_agent_needs_review(self):
        """EscalationAgent always sets needs_review=True."""
        llm = make_mock_llm("I sincerely apologize. A senior team member will follow up within 2 hours.")
        agent = EscalationAgent(llm)

        result = await agent.process(
            query="This is unacceptable! I want to speak to a manager right now!",
            entities={},
            history=[],
        )

        assert result.needs_review is True
        assert result.agent_name == "escalation_agent"
        assert result.intent == "escalation"


# ---------------------------------------------------------------------------
# Orchestrator routing test
# ---------------------------------------------------------------------------

class TestOrchestrator:
    @pytest.mark.asyncio
    async def test_orchestrator_routes_to_order_agent(self):
        """Orchestrator routes ORDER_STATUS intent to OrderAgent."""
        llm = make_mock_llm("Your order has been shipped.")
        session_store = InMemorySessionStore()
        review_queue = InMemoryReviewQueue()

        orchestrator = CustomerServiceOrchestrator(
            llm=llm,
            session_store=session_store,
            review_queue=review_queue,
            tracer=None,
        )

        order = make_order("ORD-10001", days_ago=3)
        with patch("app.agents.order_agent.get_order", return_value=order):
            result = await orchestrator.process(
                query="where is my order ORD-10001",
                session_id="test-session-001",
                request_id="req-001",
            )

        assert result.intent == "order_status"
        assert result.agent_name == "order_agent"
        assert result.session_id == "test-session-001"
        assert result.request_id == "req-001"
        assert result.latency_ms > 0


# ---------------------------------------------------------------------------
# Session store tests
# ---------------------------------------------------------------------------

class TestSessionStore:
    @pytest.mark.asyncio
    async def test_session_store_add_and_retrieve(self):
        """Adding 3 turns and retrieving them returns all 3 in order."""
        store = InMemorySessionStore()

        await store.add_turn("sess-1", "Hello", "Hi there!")
        await store.add_turn("sess-1", "What are your hours?", "We are 24/7.")
        await store.add_turn("sess-1", "Thanks", "You're welcome!")

        history = await store.get_history("sess-1")
        assert len(history) == 3
        assert history[0]["user"] == "Hello"
        assert history[0]["assistant"] == "Hi there!"
        assert history[2]["user"] == "Thanks"

    @pytest.mark.asyncio
    async def test_session_store_max_turns(self):
        """Exceeding max_turns trims oldest turns, keeping only the last N."""
        store = InMemorySessionStore(max_turns=10)

        for i in range(15):
            await store.add_turn("sess-2", f"User message {i}", f"Assistant response {i}")

        history = await store.get_history("sess-2")
        assert len(history) == 10
        # Should contain the most recent messages (5-14)
        assert history[0]["user"] == "User message 5"
        assert history[-1]["user"] == "User message 14"

    @pytest.mark.asyncio
    async def test_session_store_empty_session(self):
        """Getting history for a non-existent session returns empty list."""
        store = InMemorySessionStore()
        history = await store.get_history("nonexistent-session")
        assert history == []


# ---------------------------------------------------------------------------
# Review queue tests
# ---------------------------------------------------------------------------

class TestReviewQueue:
    @pytest.mark.asyncio
    async def test_review_queue_enqueue_dequeue(self):
        """Enqueue an item and verify dequeue returns it with queued_at timestamp."""
        queue = InMemoryReviewQueue()

        item = {
            "session_id": "sess-abc",
            "request_id": "req-xyz",
            "intent": "escalation",
            "query": "I want to speak to a manager",
            "response": "A senior rep will follow up.",
            "agent": "escalation_agent",
            "tools_called": [],
        }

        await queue.enqueue(item)
        assert await queue.size() == 1

        dequeued = await queue.dequeue()
        assert dequeued is not None
        assert dequeued["session_id"] == "sess-abc"
        assert dequeued["intent"] == "escalation"
        assert "queued_at" in dequeued
        assert await queue.size() == 0

    @pytest.mark.asyncio
    async def test_review_queue_dequeue_empty_returns_none(self):
        """Dequeuing from an empty queue returns None (non-blocking)."""
        queue = InMemoryReviewQueue()
        result = await queue.dequeue()
        assert result is None

    @pytest.mark.asyncio
    async def test_review_queue_list_pending(self):
        """list_pending returns all enqueued items for admin visibility."""
        queue = InMemoryReviewQueue()

        for i in range(3):
            await queue.enqueue({"session_id": f"sess-{i}", "intent": "escalation"})

        pending = await queue.list_pending(limit=10)
        assert len(pending) == 3
        assert pending[0]["session_id"] == "sess-0"


# ── CompoundRouter tests ─────────────────────────────────────────────────────

class TestCompoundRouter:
    def setup_method(self):
        from app.agents.compound_router import CompoundRouter
        self.router = CompoundRouter(llm=None)

    def test_single_intent_high_confidence(self):
        decision = self.router.route("where is my order ORD-10001")
        assert decision.strategy == "single"
        assert not decision.is_compound

    def test_compound_detection_and_also(self):
        decision = self.router.route(
            "I want to check my order ORD-10001 and also initiate a return"
        )
        assert decision.compound_detected is True
        # Should detect both order_status and return_request
        intents = [r[0].value for r in decision.routes]
        assert "order_status" in intents
        assert "return_request" in intents

    def test_compound_detection_as_well_as(self):
        decision = self.router.route(
            "What is the price of the headphones, as well as are they in stock?"
        )
        # "as well as" is a compound signal; but both map to product_question
        # dedup should collapse to single route
        assert decision.compound_detected is True

    def test_decompose_and_also(self):
        from app.agents.compound_router import CompoundRouter
        router = CompoundRouter()
        parts = router._decompose("check my order and also start a return")
        assert len(parts) == 2
        assert any("order" in p for p in parts)
        assert any("return" in p for p in parts)

    def test_decompose_additionally(self):
        from app.agents.compound_router import CompoundRouter
        router = CompoundRouter()
        parts = router._decompose("where is my order ORD-10005, additionally I want to know your return policy")
        assert len(parts) == 2

    def test_no_compound_signal(self):
        from app.agents.compound_router import CompoundRouter
        router = CompoundRouter()
        assert not router._has_compound_signal("where is my order")
        assert not router._has_compound_signal("I want a refund for ORD-10001")

    def test_compound_signal_detected(self):
        from app.agents.compound_router import CompoundRouter
        router = CompoundRouter()
        assert router._has_compound_signal("check order and also start return")
        assert router._has_compound_signal("track package as well as check stock")


# ── SynthesizerAgent tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSynthesizerAgent:
    def setup_method(self):
        from app.agents.synthesizer_agent import SynthesizerAgent
        from app.agents.base import AgentResult
        from unittest.mock import MagicMock, patch
        self.AgentResult = AgentResult
        self.SynthesizerAgent = SynthesizerAgent

    async def test_single_result_passthrough(self):
        mock_llm = MagicMock()
        synth = self.SynthesizerAgent(llm=mock_llm)
        result = self.AgentResult(response="Order shipped", intent="order_status", agent_name="order_agent")

        with patch.object(synth, "_invoke_llm", return_value="Order shipped") as _:
            out = await synth.process("where is my order", [result], [])
        # Single result → passthrough, no LLM called
        assert out.response == "Order shipped"

    async def test_multi_result_calls_llm(self):
        mock_llm = MagicMock()
        synth = self.SynthesizerAgent(llm=mock_llm)
        r1 = self.AgentResult(response="Order shipped on May 15", intent="order_status", agent_name="order_agent")
        r2 = self.AgentResult(response="You are eligible for a return", intent="return_request", agent_name="returns_agent")

        with patch.object(synth, "_invoke_llm", return_value="Combined response") as mock_invoke:
            out = await synth.process("check order and start return", [r1, r2], [])

        mock_invoke.assert_called_once()
        assert out.agent_name == "synthesizer_agent"
        assert "order_status" in out.intent
        assert "return_request" in out.intent

    async def test_needs_review_propagates(self):
        mock_llm = MagicMock()
        synth = self.SynthesizerAgent(llm=mock_llm)
        r1 = self.AgentResult(response="A", intent="order_status", agent_name="order_agent", needs_review=False)
        r2 = self.AgentResult(response="B", intent="return_request", agent_name="returns_agent", needs_review=True)

        with patch.object(synth, "_invoke_llm", return_value="Combined"):
            out = await synth.process("query", [r1, r2], [])

        assert out.needs_review is True


# ── Updated orchestrator compound routing tests ────────────────────────────────

@pytest.mark.asyncio
class TestOrchestratorCompound:
    async def test_orchestrator_handles_compound_query(self):
        from unittest.mock import MagicMock, patch, AsyncMock
        from app.agents.orchestrator import CustomerServiceOrchestrator
        from app.state.session import InMemorySessionStore
        from app.queue.review_queue import InMemoryReviewQueue

        mock_llm = MagicMock()
        store = InMemorySessionStore()
        queue = InMemoryReviewQueue()
        orch = CustomerServiceOrchestrator(llm=mock_llm, session_store=store, review_queue=queue)

        with patch("app.agents.base.BaseAgent._invoke_llm", new_callable=AsyncMock) as mock_invoke:
            mock_invoke.return_value = "Here is the combined answer for your order and return."
            result = await orch.process(
                query="I want to check my order ORD-10001 and also initiate a return",
                session_id="test-compound-001",
                request_id="req-001",
            )

        # Compound queries should have a non-empty response
        assert result.response != ""
        # Strategy should reflect compound routing
        assert result.routing_strategy in ("parallel", "single")
