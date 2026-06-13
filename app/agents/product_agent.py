"""Agent that handles product questions."""
from app.agents.base import BaseAgent, AgentResult
from app.tools.products import search_products, get_product, format_product_context

_SYSTEM = """You are a knowledgeable product specialist for an e-commerce store.
You have been given product catalog data below. Answer the customer's question accurately.
Include pricing and availability. If the product was not found, offer to help them search differently.
Be helpful and concise."""

class ProductAgent(BaseAgent):
    name = "product_agent"

    async def process(self, query: str, entities: dict, history: list[dict]) -> AgentResult:
        tools_called = []
        products = []

        product_id = entities.get("product_id")
        if product_id:
            p = get_product(product_id)
            tools_called.append("get_product")
            products = [p] if p else []

        if not products:
            products = search_products(query, limit=3)
            tools_called.append("search_products")

        product_context = format_product_context(products)
        history_text = self._format_history(history)

        prompt = (
            f"{_SYSTEM}\n\n"
            f"PRODUCT DATA:\n{product_context}\n\n"
            f"{history_text}"
            f"Customer: {query}\n\nAssistant:"
        )

        response = await self._invoke_llm(prompt)

        return AgentResult(
            response=response,
            intent="product_question",
            agent_name=self.name,
            tools_called=tools_called,
            tool_results={"products_found": len(products)},
        )

    def _format_history(self, history: list[dict]) -> str:
        if not history:
            return ""
        lines = ["CONVERSATION HISTORY:"]
        for turn in history[-3:]:
            lines.append(f"Customer: {turn['user']}")
            lines.append(f"Assistant: {turn['assistant']}")
        return "\n".join(lines) + "\n\n"
