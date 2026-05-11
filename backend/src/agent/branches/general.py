import logging

from brain import openai_chat_complete
from agent.state import GraphState

logger = logging.getLogger(__name__)

_GENERAL_SYSTEM = (
    "You are a helpful assistant. "
    "Answer the user's question clearly and concisely based on your general knowledge."
)


def general_answer(state: GraphState) -> GraphState:
    query = state["query"]
    logger.info("Branch general — query: %s", query)
    messages = [
        {"role": "system", "content": _GENERAL_SYSTEM},
        {"role": "user", "content": query},
    ]
    answer = openai_chat_complete(messages)
    return {
        **state,
        "generation": answer,
        "source_documents": [],
        "follow_up_questions": [],
    }
