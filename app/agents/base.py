"""Base agent class shared by all specialized agents."""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

@dataclass
class AgentResult:
    response: str
    intent: str
    agent_name: str
    tools_called: list[str] = field(default_factory=list)
    tool_results: dict[str, Any] = field(default_factory=dict)
    needs_review: bool = False
    confidence: float = 1.0
    latency_ms: float = 0.0

class BaseAgent:
    name: str = "base"

    def __init__(self, llm, tracer=None):
        self.llm = llm
        self.tracer = tracer

    async def _invoke_llm(self, prompt: str, timeout: int = 30) -> str:
        """Invoke the LLM with timeout. Returns response text."""
        start = time.perf_counter()
        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(self.llm.invoke, prompt),
                timeout=timeout,
            )
            return text.strip()
        except (TimeoutError, asyncio.TimeoutError):
            return "I apologize, I'm having trouble responding right now. Please try again."

    async def process(self, query: str, entities: dict, history: list[dict]) -> AgentResult:
        raise NotImplementedError
