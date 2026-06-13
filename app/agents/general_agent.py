"""Fallback agent for general customer service queries."""
from app.agents.base import BaseAgent, AgentResult
from app.config import SYSTEM_PROMPT

class GeneralAgent(BaseAgent):
    name = "general_agent"

    async def process(self, query: str, entities: dict, history: list[dict]) -> AgentResult:
        history_text = self._format_history(history)
        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"{history_text}"
            f"Customer: {query}\n\nAssistant:"
        )

        response = await self._invoke_llm(prompt)

        return AgentResult(
            response=response,
            intent="general",
            agent_name=self.name,
        )

    def _format_history(self, history: list[dict]) -> str:
        if not history:
            return ""
        lines = ["CONVERSATION HISTORY:"]
        for turn in history[-3:]:
            lines.append(f"Customer: {turn['user']}")
            lines.append(f"Assistant: {turn['assistant']}")
        return "\n".join(lines) + "\n\n"
