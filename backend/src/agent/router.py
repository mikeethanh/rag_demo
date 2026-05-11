import logging

from brain import openai_chat_complete
from agent.state import GraphState

logger = logging.getLogger(__name__)

_ROUTER_SYSTEM = (
    "You are a query classifier. Given a user query, classify it into exactly one category:\n"
    "- LEGAL: questions about laws, regulations, statutes, legal rights, penalties, contracts, or legal procedures\n"
    "- GENERAL: general knowledge, greetings, small talk, factual questions unrelated to law\n"
    "- CALCULATION: requests to compute a penalty amount, fine, tax, or any numerical legal result\n"
    "- AMBIGUOUS: unclear intent, current events, or topics that need web search to answer accurately\n\n"
    "Respond with ONE WORD ONLY: LEGAL, GENERAL, CALCULATION, or AMBIGUOUS."
)

_BRANCH_MAP = {
    "LEGAL": "legal",
    "GENERAL": "general",
    "CALCULATION": "calculation",
    "AMBIGUOUS": "web_search",
}


def classify(state: GraphState) -> GraphState:
    query = state["query"]
    logger.info("Router classify — query: %s", query)
    messages = [
        {"role": "system", "content": _ROUTER_SYSTEM},
        {"role": "user", "content": query},
    ]
    raw = openai_chat_complete(messages).strip().upper()
    branch = _BRANCH_MAP.get(raw, "legal")
    logger.info("Router classified as: %s → branch: %s", raw, branch)
    return {**state, "branch": branch}


def branch_router(state: GraphState) -> str:
    return state.get("branch", "legal")
