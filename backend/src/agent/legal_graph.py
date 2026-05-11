import os

from langgraph.graph import StateGraph, END

from agent.state import GraphState
from agent.nodes import rewrite, retrieve, grade_docs, generate, fallback, follow_up, rollback_router
from agent.checkpointer import get_checkpointer


def build_legal_graph(checkpointer=None):
    builder = StateGraph(GraphState)

    builder.add_node("rewrite", rewrite)
    builder.add_node("retrieve", retrieve)
    builder.add_node("grade_docs", grade_docs)
    builder.add_node("generate", generate)
    builder.add_node("fallback", fallback)
    builder.add_node("follow_up", follow_up)

    builder.set_entry_point("rewrite")
    builder.add_edge("rewrite", "retrieve")
    builder.add_edge("retrieve", "grade_docs")
    builder.add_conditional_edges(
        "grade_docs",
        rollback_router,
        {
            "generate": "generate",
            "rewrite": "rewrite",
            "fallback": "fallback",
        },
    )
    builder.add_edge("generate", "follow_up")
    builder.add_edge("follow_up", END)
    builder.add_edge("fallback", END)

    if checkpointer is None:
        checkpointer = get_checkpointer()

    return builder.compile(checkpointer=checkpointer)


legal_graph = None


def get_legal_graph():
    global legal_graph
    if legal_graph is None:
        legal_graph = build_legal_graph()
    return legal_graph
