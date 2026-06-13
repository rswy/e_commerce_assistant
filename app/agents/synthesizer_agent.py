"""SynthesizerAgent — merges multiple specialized agent responses into one."""
from app.agents.base import BaseAgent, AgentResult

_SYSTEM = """You are a customer service response synthesizer.
You have received responses from multiple specialized agents below.
Your task is to combine them into ONE clear, helpful, and natural-sounding response.

Rules:
- Do not repeat information
- Maintain a warm, professional tone
- Address everything the customer asked
- Keep it concise — under 150 words
- If agents gave conflicting information, use the more specific/detailed one"""


class SynthesizerAgent(BaseAgent):
    name = "synthesizer_agent"

    async def process(
        self,
        original_query: str,
        agent_results: list[AgentResult],
        history: list[dict],
    ) -> AgentResult:
        """Merge multiple agent responses into a single coherent reply."""
        if len(agent_results) == 1:
            return agent_results[0]

        # Build context from all agent responses
        responses_text = "\n\n".join(
            f"[{r.agent_name.replace('_', ' ').title()} response]:\n{r.response}"
            for r in agent_results
        )

        history_text = self._format_history(history)

        prompt = (
            f"{_SYSTEM}\n\n"
            f"ORIGINAL CUSTOMER QUERY: {original_query}\n\n"
            f"AGENT RESPONSES:\n{responses_text}\n\n"
            f"{history_text}"
            f"SYNTHESIZED RESPONSE:"
        )

        response = await self._invoke_llm(prompt)

        # Combine metadata from all agents
        all_tools = []
        all_tool_results = {}
        needs_review = False
        for r in agent_results:
            all_tools.extend(r.tools_called)
            all_tool_results.update(r.tool_results)
            if r.needs_review:
                needs_review = True

        return AgentResult(
            response=response,
            intent="+".join(r.intent for r in agent_results),
            agent_name=self.name,
            tools_called=list(set(all_tools)),
            tool_results=all_tool_results,
            needs_review=needs_review,
        )

    def _format_history(self, history: list[dict]) -> str:
        if not history:
            return ""
        lines = ["PRIOR CONVERSATION:"]
        for turn in history[-2:]:
            lines.append(f"Customer: {turn['user']}")
            lines.append(f"Assistant: {turn['assistant']}")
        return "\n".join(lines) + "\n\n"
