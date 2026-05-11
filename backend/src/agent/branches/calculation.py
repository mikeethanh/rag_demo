import json
import logging

from brain import openai_chat_complete
from agent.state import GraphState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stub tool definitions (OpenAI function calling schema)
# ---------------------------------------------------------------------------

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "penalty_calculator",
            "description": "Calculate the base penalty amount for a legal offense.",
            "parameters": {
                "type": "object",
                "properties": {
                    "offense_type": {
                        "type": "string",
                        "description": "Type of legal offense (e.g. tax_evasion, bribery, fraud)",
                    },
                    "base_amount": {
                        "type": "number",
                        "description": "The base amount involved in the offense (in VND)",
                    },
                },
                "required": ["offense_type", "base_amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_factors",
            "description": "Apply mitigating or aggravating factors to a base penalty.",
            "parameters": {
                "type": "object",
                "properties": {
                    "base_penalty": {
                        "type": "number",
                        "description": "The base penalty amount calculated by penalty_calculator",
                    },
                    "mitigating_factors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of mitigating factors (e.g. first_offense, voluntary_disclosure)",
                    },
                },
                "required": ["base_penalty", "mitigating_factors"],
            },
        },
    },
]

_CALC_SYSTEM = (
    "You are a legal penalty calculator assistant. "
    "Use the provided tools to calculate the penalty step by step: "
    "first call penalty_calculator to get the base penalty, "
    "then call apply_factors with any relevant mitigating factors. "
    "Finally, summarize the result in a clear sentence."
)


def _run_tool(name: str, args: dict) -> str:
    """Stub tool executor — returns hardcoded realistic values."""
    if name == "penalty_calculator":
        offense = args.get("offense_type", "unknown")
        base = float(args.get("base_amount", 0))
        # Stub: base penalty is 20% of the amount, minimum 5,000,000 VND
        base_penalty = max(base * 0.20, 5_000_000)
        return json.dumps({"base_penalty": base_penalty, "offense_type": offense})

    if name == "apply_factors":
        base = float(args.get("base_penalty", 0))
        factors = args.get("mitigating_factors", [])
        reduction = 0.0
        if "first_offense" in factors:
            reduction += 0.10
        if "voluntary_disclosure" in factors:
            reduction += 0.15
        if "cooperation" in factors:
            reduction += 0.05
        final = base * (1.0 - min(reduction, 0.30))
        return json.dumps({"final_penalty": final, "reduction_applied": reduction})

    return json.dumps({"error": f"unknown tool: {name}"})


def calculation_answer(state: GraphState) -> GraphState:
    query = state["query"]
    logger.info("Branch calculation — query: %s", query)

    messages = [
        {"role": "system", "content": _CALC_SYSTEM},
        {"role": "user", "content": query},
    ]

    # Agentic tool-call loop (max 4 turns to prevent runaway)
    for _ in range(4):
        raw = openai_chat_complete(messages, raw=True, tools=_TOOLS)

        if raw.tool_calls:
            # Process each tool call and append results
            messages.append({"role": "assistant", "content": raw.content, "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in raw.tool_calls
            ]})
            for tc in raw.tool_calls:
                args = json.loads(tc.function.arguments)
                result = _run_tool(tc.function.name, args)
                logger.info("Tool %s → %s", tc.function.name, result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
        else:
            # LLM produced a final text answer — done
            answer = raw.content or ""
            return {
                **state,
                "generation": answer,
                "source_documents": [],
                "follow_up_questions": [],
            }

    # Fallback if loop exhausted without a text answer
    return {
        **state,
        "generation": "Unable to calculate the penalty. Please provide more details.",
        "source_documents": [],
        "follow_up_questions": [],
    }
