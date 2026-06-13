"""Agent that handles order status and tracking queries."""
import asyncio
from app.agents.base import BaseAgent, AgentResult
from app.tools.orders import get_order, format_order_context

_SYSTEM = """You are a customer service agent for an e-commerce store.
You have been given real order data below. Answer the customer's question accurately using ONLY this data.
If the order was not found, apologize and ask them to verify the order number.
Be concise and professional. Do not invent information."""

class OrderAgent(BaseAgent):
    name = "order_agent"

    async def process(self, query: str, entities: dict, history: list[dict]) -> AgentResult:
        tools_called = []
        tool_results = {}

        # Tool call: look up order
        order_id = entities.get("order_id")
        order = None
        if order_id:
            order = get_order(order_id)
            tools_called.append("get_order")
            tool_results["order"] = order

        order_context = format_order_context(order)

        history_text = self._format_history(history)
        prompt = (
            f"{_SYSTEM}\n\n"
            f"ORDER DATA:\n{order_context}\n\n"
            f"{history_text}"
            f"Customer: {query}\n\nAssistant:"
        )

        response = await self._invoke_llm(prompt)

        return AgentResult(
            response=response,
            intent="order_status",
            agent_name=self.name,
            tools_called=tools_called,
            tool_results={"order_found": order is not None},
        )

    def _format_history(self, history: list[dict]) -> str:
        if not history:
            return ""
        lines = ["CONVERSATION HISTORY:"]
        for turn in history[-3:]:  # Last 3 turns
            lines.append(f"Customer: {turn['user']}")
            lines.append(f"Assistant: {turn['assistant']}")
        lines.append("")
        return "\n".join(lines) + "\n"
