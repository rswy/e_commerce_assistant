"""Agent that handles escalation requests — logs to review queue and responds with empathy."""
from app.agents.base import BaseAgent, AgentResult

_SYSTEM = """You are a senior customer service representative for an e-commerce store.
The customer is upset and has requested escalation or a manager.
Your response should:
1. Acknowledge their frustration sincerely
2. Take ownership of their experience
3. Assure them that a senior team member will follow up within 2 business hours
4. Ask for their contact preference (email or phone) if not already provided
Be warm, professional, and avoid defensive language."""

class EscalationAgent(BaseAgent):
    name = "escalation_agent"

    async def process(self, query: str, entities: dict, history: list[dict]) -> AgentResult:
        history_text = self._format_history(history)
        prompt = (
            f"{_SYSTEM}\n\n"
            f"{history_text}"
            f"Customer: {query}\n\nAssistant:"
        )

        response = await self._invoke_llm(prompt)

        return AgentResult(
            response=response,
            intent="escalation",
            agent_name=self.name,
            tools_called=[],
            tool_results={},
            needs_review=True,  # Always route escalations to human review
            confidence=1.0,
        )

    def _format_history(self, history: list[dict]) -> str:
        if not history:
            return ""
        lines = ["CONVERSATION HISTORY:"]
        for turn in history[-5:]:  # More history for escalations
            lines.append(f"Customer: {turn['user']}")
            lines.append(f"Assistant: {turn['assistant']}")
        return "\n".join(lines) + "\n\n"
