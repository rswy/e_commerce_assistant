"""Agent that handles return and refund requests."""
from app.agents.base import BaseAgent, AgentResult
from app.tools.orders import get_order
from app.tools.returns import check_return_eligibility, format_return_context

_SYSTEM = """You are a customer service agent handling returns and refunds for an e-commerce store.
You have been given return eligibility data below.
- If eligible: guide the customer through the return process (visit returns portal at returns.example.com, pack items, attach label).
- If not eligible: apologize and explain why, offer alternatives (store credit, repair, partial refund consideration).
Be empathetic and professional."""

class ReturnsAgent(BaseAgent):
    name = "returns_agent"

    async def process(self, query: str, entities: dict, history: list[dict]) -> AgentResult:
        tools_called = []
        tool_results = {}

        order_id = entities.get("order_id")
        order = None
        eligibility = {"eligible": False, "reason": "No order ID provided to check eligibility."}

        if order_id:
            order = get_order(order_id)
            tools_called.append("get_order")
            if order:
                eligibility = check_return_eligibility(order)
                tools_called.append("check_return_eligibility")
                tool_results["eligibility"] = eligibility

        return_context = format_return_context(eligibility)
        history_text = self._format_history(history)

        prompt = (
            f"{_SYSTEM}\n\n"
            f"RETURN ELIGIBILITY:\n{return_context}\n\n"
            f"{history_text}"
            f"Customer: {query}\n\nAssistant:"
        )

        response = await self._invoke_llm(prompt)

        return AgentResult(
            response=response,
            intent="return_request",
            agent_name=self.name,
            tools_called=tools_called,
            tool_results=tool_results,
            needs_review=not eligibility.get("eligible", False),
        )

    def _format_history(self, history: list[dict]) -> str:
        if not history:
            return ""
        lines = ["CONVERSATION HISTORY:"]
        for turn in history[-3:]:
            lines.append(f"Customer: {turn['user']}")
            lines.append(f"Assistant: {turn['assistant']}")
        return "\n".join(lines) + "\n\n"
