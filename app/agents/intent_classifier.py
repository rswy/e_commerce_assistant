"""Intent classification and entity extraction for customer queries."""
import re
from dataclasses import dataclass, field
from enum import Enum

class Intent(str, Enum):
    ORDER_STATUS = "order_status"
    RETURN_REQUEST = "return_request"
    PRODUCT_QUESTION = "product_question"
    ACCOUNT_ISSUE = "account_issue"
    ESCALATION = "escalation"
    GENERAL = "general"

@dataclass
class ClassificationResult:
    intent: Intent
    confidence: float
    entities: dict = field(default_factory=dict)

class IntentClassifier:
    # Keyword sets ordered by specificity (most specific first)
    _INTENT_KEYWORDS: dict[Intent, list[str]] = {
        Intent.ESCALATION: [
            "speak to a manager", "speak to manager", "supervisor", "escalate",
            "this is unacceptable", "legal action", "complaint", "disgusted",
            "furious", "terrible service", "worst", "i want to speak",
        ],
        Intent.RETURN_REQUEST: [
            "return", "refund", "exchange", "send back", "damaged", "broken",
            "wrong item", "not as described", "want my money back", "money back",
            "defective", "rma",
        ],
        Intent.ORDER_STATUS: [
            "where is my order", "track my order", "order status", "order number",
            "tracking number", "when will", "shipment", "shipped", "delivery",
            "arrived", "package", "still haven't received", "ord-", "order #",
        ],
        Intent.PRODUCT_QUESTION: [
            "how much", "price", "cost", "in stock", "available", "buy", "purchase",
            "product", "item", "dimensions", "weight", "color", "size", "sku",
            "description", "specifications", "specs", "does it come",
        ],
        Intent.ACCOUNT_ISSUE: [
            "account", "log in", "login", "password", "reset password", "sign in",
            "can't access", "profile", "email address", "update my", "change my",
        ],
    }

    # Entity patterns
    _ORDER_ID_RE = re.compile(
        r'\b(?:ORD[-–]?\s*(\d{5,})|order\s+#?\s*(\d{5,})|#(\d{5,}))\b',
        re.IGNORECASE,
    )
    _PRODUCT_ID_RE = re.compile(r'\b(SKU[-–]?\w{3,}|P\d{3,})\b', re.IGNORECASE)
    _RETURN_ID_RE = re.compile(r'\b(RMA[-–]?\d{4,}|return\s+#?\s*\d{4,})\b', re.IGNORECASE)

    def classify(self, text: str) -> ClassificationResult:
        """Classify intent and extract entities from user text."""
        text_lower = text.lower()
        entities = self._extract_entities(text)

        for intent, keywords in self._INTENT_KEYWORDS.items():
            for kw in keywords:
                if kw in text_lower:
                    confidence = 0.9 if len(kw.split()) > 2 else 0.75
                    return ClassificationResult(intent=intent, confidence=confidence, entities=entities)

        # Fallback: if order_id present, assume order status
        if entities.get("order_id"):
            return ClassificationResult(intent=Intent.ORDER_STATUS, confidence=0.6, entities=entities)

        return ClassificationResult(intent=Intent.GENERAL, confidence=0.5, entities=entities)

    def _extract_entities(self, text: str) -> dict:
        entities: dict = {}

        m = self._ORDER_ID_RE.search(text)
        if m:
            raw = next(g for g in m.groups() if g)
            entities["order_id"] = f"ORD-{raw.lstrip('0') or '0'}"

        m = self._PRODUCT_ID_RE.search(text)
        if m:
            entities["product_id"] = m.group(1).upper()

        m = self._RETURN_ID_RE.search(text)
        if m:
            entities["return_id"] = m.group(1).upper()

        return entities
