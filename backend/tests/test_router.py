"""
Phase 2 test gate — Semantic Router.

Tests classify() node and branch_router() conditional edge.
All LLM calls are mocked.
"""
import pytest
from unittest.mock import patch

from agent.state import GraphState
from agent.router import classify, branch_router


def _base_state(query: str) -> GraphState:
    return GraphState(
        query=query,
        documents=[],
        generation="",
        transformation_count=0,
        follow_up_questions=[],
        source_documents=[],
    )


# ---------------------------------------------------------------------------
# classify() node — LLM output → branch field
# ---------------------------------------------------------------------------

class TestClassifyNode:
    @pytest.mark.parametrize("llm_output,expected_branch", [
        ("LEGAL",       "legal"),
        ("legal",       "legal"),   # case-insensitive
        ("GENERAL",     "general"),
        ("CALCULATION", "calculation"),
        ("AMBIGUOUS",   "web_search"),
        ("UNKNOWN",     "legal"),   # unrecognised → default legal
        ("  LEGAL  ",   "legal"),   # whitespace stripped
    ])
    def test_classify_maps_llm_output_to_branch(self, llm_output, expected_branch):
        state = _base_state("some query")
        with patch("agent.router.openai_chat_complete", return_value=llm_output):
            result = classify(state)
        assert result["branch"] == expected_branch

    def test_classify_preserves_query(self):
        state = _base_state("What is the fine for bribery?")
        with patch("agent.router.openai_chat_complete", return_value="LEGAL"):
            result = classify(state)
        assert result["query"] == "What is the fine for bribery?"


# ---------------------------------------------------------------------------
# branch_router() conditional edge — reads branch field
# ---------------------------------------------------------------------------

class TestBranchRouter:
    @pytest.mark.parametrize("branch", ["legal", "general", "calculation", "web_search"])
    def test_routes_to_correct_branch(self, branch):
        state = _base_state("q")
        state["branch"] = branch
        assert branch_router(state) == branch

    def test_defaults_to_legal_when_branch_missing(self):
        state = _base_state("q")
        assert branch_router(state) == "legal"
