"""LLM-as-judge evaluator for customer service response quality.

Uses Claude as the judge model to assess responses along four dimensions:
  - accuracy:      Does the response use correct information?
  - helpfulness:   Does it address the customer's actual need?
  - tone:          Is it professional, empathetic, and not robotic?
  - completeness:  Does it provide all information the customer needs?

Falls back to a keyword-based heuristic scorer when ANTHROPIC_API_KEY is not set.

Usage:
    evaluator = LLMEvaluator()
    result = evaluator.evaluate(
        question="Where is my order ORD-10001?",
        response="Your order ORD-10001 was shipped on May 15 and is expected...",
        intent="order_status",
    )
    print(result)  # EvaluationResult(accuracy=0.9, helpfulness=0.85, ...)
"""
import json
import os
import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

@dataclass
class EvaluationResult:
    accuracy: float      # 0.0 – 1.0
    helpfulness: float
    tone: float
    completeness: float
    overall: float
    judge_model: str     # which model/method produced the score
    raw_response: str = ""

    def to_dict(self) -> dict:
        return {
            "accuracy": self.accuracy,
            "helpfulness": self.helpfulness,
            "tone": self.tone,
            "completeness": self.completeness,
            "overall": self.overall,
            "judge_model": self.judge_model,
        }


_JUDGE_SYSTEM = """You are an expert customer service quality evaluator.
Score the assistant response on four dimensions, each from 0.0 to 1.0:
- accuracy:      Is the information factually correct and consistent with the context provided?
- helpfulness:   Does the response directly address what the customer needs?
- tone:          Is the response professional, warm, and appropriately empathetic?
- completeness:  Does it provide all the information the customer needs to take action?

Return ONLY a valid JSON object with keys: accuracy, helpfulness, tone, completeness, overall.
The "overall" field should be your holistic score (not necessarily the average).
Example: {"accuracy": 0.9, "helpfulness": 0.85, "tone": 0.95, "completeness": 0.8, "overall": 0.88}"""

_JUDGE_USER_TEMPLATE = """Customer question: {question}

Assistant response: {response}

Intent category: {intent}

Score this response."""


class LLMEvaluator:
    """Evaluates responses using Claude as judge, with heuristic fallback."""

    def __init__(self):
        self._api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._client = None
        self._judge_model = "claude-haiku-4-5-20251001"

        if self._api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
                logger.info("llm_evaluator_initialized", model=self._judge_model)
            except ImportError:
                logger.warning("anthropic_sdk_not_installed", fallback="heuristic")
                self._client = None
        else:
            logger.info("llm_evaluator_heuristic_mode", reason="ANTHROPIC_API_KEY not set")

    def evaluate(
        self,
        question: str,
        response: str,
        intent: str = "general",
    ) -> EvaluationResult:
        """Evaluate a single question/response pair."""
        if self._client:
            return self._evaluate_with_claude(question, response, intent)
        return self._evaluate_heuristic(question, response, intent)

    def evaluate_batch(
        self,
        items: list[dict],
    ) -> list[EvaluationResult]:
        """Evaluate a list of {"question": ..., "response": ..., "intent": ...} dicts."""
        return [self.evaluate(**item) for item in items]

    def _evaluate_with_claude(self, question: str, response: str, intent: str) -> EvaluationResult:
        user_msg = _JUDGE_USER_TEMPLATE.format(
            question=question[:500],
            response=response[:1000],
            intent=intent,
        )
        try:
            message = self._client.messages.create(
                model=self._judge_model,
                max_tokens=256,
                system=_JUDGE_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = message.content[0].text.strip()
            # Extract JSON — Claude sometimes wraps it in ```json ... ```
            json_match = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
            if json_match:
                scores = json.loads(json_match.group())
            else:
                scores = json.loads(raw)

            return EvaluationResult(
                accuracy=float(scores.get("accuracy", 0.5)),
                helpfulness=float(scores.get("helpfulness", 0.5)),
                tone=float(scores.get("tone", 0.5)),
                completeness=float(scores.get("completeness", 0.5)),
                overall=float(scores.get("overall", 0.5)),
                judge_model=self._judge_model,
                raw_response=raw,
            )
        except Exception as exc:
            logger.warning("llm_evaluation_failed", error=str(exc), fallback="heuristic")
            return self._evaluate_heuristic(question, response, intent)

    def _evaluate_heuristic(self, question: str, response: str, intent: str) -> EvaluationResult:
        """Rule-based fallback scorer when Claude API is unavailable."""
        resp_lower = response.lower()
        words = len(response.split())

        # Tone signals
        empathy_words = ["apologize", "sorry", "understand", "happy to help", "thank you"]
        tone = 0.5 + 0.1 * sum(1 for w in empathy_words if w in resp_lower)
        tone = min(tone, 1.0)

        # Completeness: penalise very short answers
        completeness = min(words / 30, 1.0)

        # Helpfulness: check for relevant keywords per intent
        intent_keywords = {
            "order_status": ["order", "tracking", "delivery", "shipped", "status"],
            "return_request": ["return", "refund", "portal", "eligible", "30 days"],
            "product_question": ["price", "stock", "available", "$", "description"],
            "escalation": ["team member", "follow up", "business hours", "contact"],
            "general": ["help", "assist", "service"],
        }
        keywords = intent_keywords.get(intent, intent_keywords["general"])
        helpfulness = min(sum(0.2 for kw in keywords if kw in resp_lower), 1.0) or 0.5

        # Accuracy: heuristic can't verify facts, assume moderate
        accuracy = 0.7

        overall = round((accuracy + helpfulness + tone + completeness) / 4, 3)

        return EvaluationResult(
            accuracy=accuracy,
            helpfulness=round(helpfulness, 3),
            tone=round(tone, 3),
            completeness=round(completeness, 3),
            overall=overall,
            judge_model="heuristic",
        )
