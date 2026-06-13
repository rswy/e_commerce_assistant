"""Compound intent router — three-phase cascade.

Phase 1: Keyword classifier (fast, deterministic, ≥85% confidence → single agent)
Phase 2: Multi-intent decomposition (compound queries → parallel agents)
Phase 3: LLM-based disambiguation (low confidence queries)
"""
import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional

import structlog

from app.agents.intent_classifier import IntentClassifier, Intent, ClassificationResult

logger = structlog.get_logger(__name__)

# ── Compound signal patterns ──────────────────────────────────────────────────
# These phrases reliably indicate a query spans multiple intents.
_COMPOUND_PATTERNS = [
    r"\band\s+also\b",
    r"\bas\s+well\s+as\b",
    r"\badditionally\b",
    r"\bfurthermore\b",
    r"\bplus\b",
    r"\bcould\s+you\s+also\b",
    r"\balso\s+want\s+to\b",
    r"\balso\s+need\s+to\b",
    r"\balso\s+check\b",
    r"\bat\s+the\s+same\s+time\b",
    r"\bseparate\s+question\b",
    r"\banother\s+thing\b",
    r"\bone\s+more\s+thing\b",
]
_COMPOUND_RE = re.compile("|".join(_COMPOUND_PATTERNS), re.IGNORECASE)

# ── Split boundaries ─────────────────────────────────────────────────────────
_SPLIT_RE = re.compile(
    r"(?:,?\s*(?:and\s+also|as\s+well\s+as|additionally|furthermore|plus"
    r"|could\s+you\s+also|also\s+want\s+to|also\s+need\s+to|also\s+check"
    r"|also|at\s+the\s+same\s+time|separate\s+question|another\s+thing"
    r"|one\s+more\s+thing)[\s,]*)",
    re.IGNORECASE,
)

# LLM disambiguation prompt — used only when keyword confidence is low
_LLM_DISAMBIGUATE_PROMPT = """You are a query classifier for a customer service system.

Classify the customer query into EXACTLY ONE of these categories:
- order_status: asking about order location, tracking, delivery, shipment
- return_request: requesting a return, refund, exchange, or reporting damage
- product_question: asking about product price, availability, specs, or description
- account_issue: asking about login, password, profile, or account access
- escalation: requesting a manager, supervisor, or expressing serious dissatisfaction
- general: anything else related to customer service

Customer query: {query}

Respond with ONLY the category name, nothing else."""


@dataclass
class RoutingDecision:
    """Describes how to route a query."""
    strategy: str  # "single" | "parallel" | "llm_routed"
    routes: list[tuple[Intent, str]]  # (intent, sub_query) pairs
    classification_confidence: float
    compound_detected: bool = False

    @property
    def is_compound(self) -> bool:
        return len(self.routes) > 1


class CompoundRouter:
    """Three-phase compound intent router."""

    SINGLE_AGENT_CONFIDENCE_THRESHOLD = 0.85
    LLM_FALLBACK_CONFIDENCE_THRESHOLD = 0.60

    def __init__(self, llm=None):
        self._classifier = IntentClassifier()
        self._llm = llm

    def route(self, query: str) -> RoutingDecision:
        """Synchronous routing decision — fast phases only."""
        # Phase 1: Keyword classifier
        result = self._classifier.classify(query)

        if result.confidence >= self.SINGLE_AGENT_CONFIDENCE_THRESHOLD:
            if not self._has_compound_signal(query):
                logger.debug(
                    "router_single_fast_path",
                    intent=result.intent.value,
                    confidence=result.confidence,
                )
                return RoutingDecision(
                    strategy="single",
                    routes=[(result.intent, query)],
                    classification_confidence=result.confidence,
                    compound_detected=False,
                )

        # Phase 2: Compound signal detection + decomposition
        if self._has_compound_signal(query):
            sub_queries = self._decompose(query)
            routes: list[tuple[Intent, str]] = []
            seen_intents: set[Intent] = set()

            for sq in sub_queries:
                sq_result = self._classifier.classify(sq)
                if sq_result.intent not in seen_intents:
                    routes.append((sq_result.intent, sq))
                    seen_intents.add(sq_result.intent)

            if len(routes) > 1:
                logger.info(
                    "router_compound_detected",
                    intents=[r[0].value for r in routes],
                    sub_queries=sub_queries,
                )
                return RoutingDecision(
                    strategy="parallel",
                    routes=routes,
                    classification_confidence=result.confidence,
                    compound_detected=True,
                )

        # Single intent (even if compound signal fired but deduplication left one)
        logger.debug(
            "router_single_standard",
            intent=result.intent.value,
            confidence=result.confidence,
        )
        return RoutingDecision(
            strategy="single",
            routes=[(result.intent, query)],
            classification_confidence=result.confidence,
            compound_detected=self._has_compound_signal(query),
        )

    async def route_with_llm_fallback(self, query: str) -> RoutingDecision:
        """Full routing with LLM fallback for low-confidence queries."""
        decision = self.route(query)

        # If single-agent and confidence is low, try LLM disambiguation
        if (
            not decision.is_compound
            and decision.classification_confidence < self.LLM_FALLBACK_CONFIDENCE_THRESHOLD
            and self._llm is not None
        ):
            llm_intent = await self._llm_disambiguate(query)
            if llm_intent is not None:
                logger.info(
                    "router_llm_fallback_used",
                    original_intent=decision.routes[0][0].value,
                    llm_intent=llm_intent.value,
                    confidence=decision.classification_confidence,
                )
                return RoutingDecision(
                    strategy="llm_routed",
                    routes=[(llm_intent, query)],
                    classification_confidence=decision.classification_confidence,
                    compound_detected=False,
                )

        return decision

    def _has_compound_signal(self, query: str) -> bool:
        return bool(_COMPOUND_RE.search(query))

    def _decompose(self, query: str) -> list[str]:
        """Split a compound query into sub-queries on compound signals."""
        parts = _SPLIT_RE.split(query)
        cleaned = [p.strip().strip(",").strip() for p in parts if p and p.strip()]
        # Filter out fragments that are too short to be meaningful
        return [p for p in cleaned if len(p.split()) >= 2] or [query]

    async def _llm_disambiguate(self, query: str) -> Optional[Intent]:
        """Ask the LLM to classify the query intent. Returns None on failure."""
        try:
            prompt = _LLM_DISAMBIGUATE_PROMPT.format(query=query[:300])
            raw = await asyncio.wait_for(
                asyncio.to_thread(self._llm.invoke, prompt),
                timeout=10,
            )
            raw = raw.strip().lower().replace("-", "_")
            # Map response to Intent enum
            intent_map = {i.value: i for i in Intent}
            return intent_map.get(raw)
        except Exception as exc:
            logger.warning("llm_disambiguation_failed", error=str(exc))
            return None
