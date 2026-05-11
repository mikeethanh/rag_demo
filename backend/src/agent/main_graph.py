import logging

from langgraph.graph import StateGraph, END

from agent.state import GraphState
from agent.router import classify, branch_router
from agent.nodes import rewrite, retrieve, grade_docs, generate, fallback, follow_up, rollback_router
from agent.branches.general import general_answer
from agent.branches.calculation import calculation_answer
from agent.branches.web_search import web_search_answer
from agent.checkpointer import get_checkpointer

logger = logging.getLogger(__name__)

_main_graph = None


def build_main_graph(checkpointer=None):
    builder = StateGraph(GraphState)

    # ── Router ──────────────────────────────────────────────────────────────
    builder.add_node("classify", classify)

    # ── Legal branch nodes ───────────────────────────────────────────────────
    builder.add_node("rewrite", rewrite)
    builder.add_node("retrieve", retrieve)
    builder.add_node("grade_docs", grade_docs)
    builder.add_node("generate", generate)
    builder.add_node("fallback", fallback)
    builder.add_node("follow_up", follow_up)

    # ── Other branch nodes ───────────────────────────────────────────────────
    builder.add_node("general_answer", general_answer)
    builder.add_node("calculation_answer", calculation_answer)
    builder.add_node("web_search_answer", web_search_answer)

    # ── Entry ────────────────────────────────────────────────────────────────
    builder.set_entry_point("classify")

    # ── Router → branches ────────────────────────────────────────────────────
    builder.add_conditional_edges(
        "classify",
        branch_router,
        {
            "legal": "rewrite",
            "general": "general_answer",
            "calculation": "calculation_answer",
            "web_search": "web_search_answer",
        },
    )

    # ── Legal branch internal edges ──────────────────────────────────────────
    builder.add_edge("rewrite", "retrieve")
    builder.add_edge("retrieve", "grade_docs")
    builder.add_conditional_edges(
        "grade_docs",
        rollback_router,
        {"generate": "generate", "rewrite": "rewrite", "fallback": "fallback"},
    )
    builder.add_edge("generate", "follow_up")
    builder.add_edge("follow_up", END)
    builder.add_edge("fallback", END)

    # ── Other branches → END ─────────────────────────────────────────────────
    builder.add_edge("general_answer", END)
    builder.add_edge("calculation_answer", END)
    builder.add_edge("web_search_answer", END)

    if checkpointer is None:
        checkpointer = get_checkpointer()
    return builder.compile(checkpointer=checkpointer)


def get_main_graph():
    global _main_graph
    if _main_graph is None:
        _main_graph = build_main_graph()
    return _main_graph
