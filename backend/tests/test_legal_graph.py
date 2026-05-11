"""
Phase 1 test gate — LangGraph Legal RAG core.

All LLM and Qdrant calls are mocked. Tests verify graph structure,
conditional routing, and checkpointer state persistence.
"""
import pytest
from unittest.mock import patch, MagicMock
from langgraph.checkpoint.memory import MemorySaver

from agent.state import GraphState
from agent.nodes import rollback_router, rewrite, retrieve, grade_docs, generate, fallback, follow_up
from agent.legal_graph import build_legal_graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_state(**overrides) -> GraphState:
    base = GraphState(
        query="What is the penalty for tax evasion?",
        documents=[],
        generation="",
        transformation_count=0,
        follow_up_questions=[],
        source_documents=[],
    )
    base.update(overrides)
    return base


def _fake_docs(n=2):
    return [{"title": f"Doc {i}", "content": f"Content {i}", "source": "", "page": ""} for i in range(n)]


# ---------------------------------------------------------------------------
# rollback_router unit tests (no LLM calls)
# ---------------------------------------------------------------------------

class TestRollbackRouter:
    def test_routes_to_generate_when_score_high(self):
        state = _base_state(**{"_grade_avg": 0.8, "transformation_count": 1})
        assert rollback_router(state) == "generate"

    def test_routes_to_generate_at_exactly_07(self):
        state = _base_state(**{"_grade_avg": 0.7, "transformation_count": 1})
        assert rollback_router(state) == "generate"

    def test_routes_to_rewrite_when_score_low_count_under_3(self):
        state = _base_state(**{"_grade_avg": 0.5, "transformation_count": 2})
        assert rollback_router(state) == "rewrite"

    def test_routes_to_fallback_when_score_low_count_at_3(self):
        state = _base_state(**{"_grade_avg": 0.5, "transformation_count": 3})
        assert rollback_router(state) == "fallback"

    def test_routes_to_fallback_when_score_low_count_over_3(self):
        state = _base_state(**{"_grade_avg": 0.3, "transformation_count": 5})
        assert rollback_router(state) == "fallback"


# ---------------------------------------------------------------------------
# Node unit tests (mock LLM / Qdrant)
# ---------------------------------------------------------------------------

class TestRewriteNode:
    def test_increments_transformation_count(self):
        state = _base_state(transformation_count=1)
        with patch("agent.nodes.openai_chat_complete", return_value="rewritten query"):
            result = rewrite(state)
        assert result["transformation_count"] == 2
        assert result["query"] == "rewritten query"


class TestRetrieveNode:
    def test_populates_documents_and_sources(self):
        state = _base_state()
        fake = [{"title": "Tax Law", "content": "...", "source": "tax.pdf", "page": "3"}]
        with patch("agent.nodes.get_embedding", return_value=[0.1] * 3072), \
             patch("agent.nodes.search_vector", return_value=fake):
            result = retrieve(state)
        assert len(result["documents"]) == 1
        assert result["source_documents"][0]["title"] == "Tax Law"
        assert result["source_documents"][0]["page"] == "3"


class TestGradeDocsNode:
    def test_sets_grade_avg_from_scores(self):
        state = _base_state(documents=_fake_docs(2))
        with patch("agent.nodes.openai_chat_complete", return_value="0.9"):
            result = grade_docs(state)
        assert abs(result["_grade_avg"] - 0.9) < 0.01

    def test_zero_avg_when_no_documents(self):
        state = _base_state(documents=[])
        result = grade_docs(state)
        assert result["_grade_avg"] == 0.0

    def test_handles_malformed_score_gracefully(self):
        state = _base_state(documents=_fake_docs(1))
        with patch("agent.nodes.openai_chat_complete", return_value="not_a_number"):
            result = grade_docs(state)
        assert result["_grade_avg"] == 0.0


class TestGenerateNode:
    def test_sets_generation(self):
        state = _base_state(documents=_fake_docs(2))
        with patch("agent.nodes.openai_chat_complete", return_value="The penalty is X [Doc 1]."):
            result = generate(state)
        assert result["generation"] == "The penalty is X [Doc 1]."


class TestFallbackNode:
    def test_sets_generation_and_clears_sources(self):
        state = _base_state(documents=_fake_docs(2), source_documents=[{"title": "x"}])
        with patch("agent.nodes.openai_chat_complete", return_value="Based on general knowledge..."):
            result = fallback(state)
        assert "general knowledge" in result["generation"]
        assert result["source_documents"] == []
        assert result["follow_up_questions"] == []


class TestFollowUpNode:
    def test_parses_three_questions(self):
        state = _base_state(
            documents=_fake_docs(2),
            generation="The answer is X.",
        )
        llm_output = "1. What are the mitigating factors?\n2. Can it be appealed?\n3. What is the statute of limitations?"
        with patch("agent.nodes.openai_chat_complete", return_value=llm_output):
            result = follow_up(state)
        assert len(result["follow_up_questions"]) == 3
        assert "mitigating" in result["follow_up_questions"][0]


# ---------------------------------------------------------------------------
# Full graph integration tests
# ---------------------------------------------------------------------------

class TestLegalGraphEndToEnd:
    def _mock_llm_side_effect(self, messages, **kwargs):
        """Return different values depending on which node is calling."""
        system = messages[0]["content"] if messages else ""
        if "optimizer" in system:
            return "penalty for tax evasion in Vietnam"
        if "grader" in system:
            return "0.85"
        if "precise legal assistant" in system:
            return "The penalty is imprisonment [Doc 1]."
        if "research assistant" in system:
            return "1. What is the appeal process?\n2. Are there mitigating factors?\n3. What court handles this?"
        return "default response"

    def test_happy_path_reaches_follow_up(self):
        checkpointer = MemorySaver()
        graph = build_legal_graph(checkpointer=checkpointer)
        fake_docs = _fake_docs(2)

        with patch("agent.nodes.openai_chat_complete", side_effect=self._mock_llm_side_effect), \
             patch("agent.nodes.get_embedding", return_value=[0.0] * 3072), \
             patch("agent.nodes.search_vector", return_value=fake_docs):
            config = {"configurable": {"thread_id": "test-happy"}}
            result = graph.invoke(
                {"query": "What is the penalty for tax evasion?", "transformation_count": 0},
                config=config,
            )

        assert result["generation"] != ""
        assert len(result["follow_up_questions"]) == 3
        assert isinstance(result["source_documents"], list)

    def test_rollback_then_pass(self):
        """First grade returns low score → rewrite → second grade returns high score → generate."""
        checkpointer = MemorySaver()
        graph = build_legal_graph(checkpointer=checkpointer)
        fake_docs = _fake_docs(2)

        grade_node_visits = {"count": 0}

        def llm_side_effect(messages, **kwargs):
            system = messages[0]["content"] if messages else ""
            if "optimizer" in system:
                return "rewritten query"
            if "grader" in system:
                # Each grade_docs node visit scores all docs, then the conditional fires.
                # We track visits by counting unique (node_visit, doc) combos via transformation_count
                # in the user message to distinguish first vs second grade node visit.
                user_msg = messages[1]["content"] if len(messages) > 1 else ""
                # After first rewrite transformation_count==1, after second==2
                if "rewritten query" in user_msg or grade_node_visits["count"] < 2:
                    grade_node_visits["count"] += 1
                    return "0.3" if grade_node_visits["count"] <= 2 else "0.9"
                return "0.9"
            if "precise legal" in system:
                return "Answer with citation [Doc 1]."
            if "research assistant" in system:
                return "1. Q1\n2. Q2\n3. Q3"
            return "ok"

        with patch("agent.nodes.openai_chat_complete", side_effect=llm_side_effect), \
             patch("agent.nodes.get_embedding", return_value=[0.0] * 3072), \
             patch("agent.nodes.search_vector", return_value=fake_docs):
            config = {"configurable": {"thread_id": "test-rollback"}}
            result = graph.invoke(
                {"query": "penalty query", "transformation_count": 0},
                config=config,
            )

        # rewrite ran at least twice (once initially, once after rollback)
        assert result["transformation_count"] >= 2
        assert "citation" in result["generation"]

    def test_fallback_after_max_retries(self):
        checkpointer = MemorySaver()
        graph = build_legal_graph(checkpointer=checkpointer)
        fake_docs = _fake_docs(2)

        def llm_side_effect(messages, **kwargs):
            system = messages[0]["content"] if messages else ""
            if "optimizer" in system:
                return "rewritten"
            if "grader" in system:
                return "0.1"  # always low → force fallback after 3 rewrites
            if "not sourced" in system or "general" in system.lower():
                return "Fallback answer without citations."
            return "ok"

        with patch("agent.nodes.openai_chat_complete", side_effect=llm_side_effect), \
             patch("agent.nodes.get_embedding", return_value=[0.0] * 3072), \
             patch("agent.nodes.search_vector", return_value=fake_docs):
            config = {"configurable": {"thread_id": "test-fallback"}}
            result = graph.invoke(
                {"query": "obscure query", "transformation_count": 0},
                config=config,
            )

        assert result["transformation_count"] >= 3
        assert result["source_documents"] == []
        assert result["follow_up_questions"] == []
        assert result["generation"] != ""

    def test_checkpointer_persists_state_across_invocations(self):
        checkpointer = MemorySaver()
        graph = build_legal_graph(checkpointer=checkpointer)
        fake_docs = _fake_docs(1)
        thread = "test-memory"

        def llm_ok(messages, **kwargs):
            system = messages[0]["content"] if messages else ""
            if "optimizer" in system:
                return "rewritten"
            if "grader" in system:
                return "0.9"
            if "precise" in system:
                return "First answer."
            if "research assistant" in system:
                return "1. Q1\n2. Q2\n3. Q3"
            return "ok"

        with patch("agent.nodes.openai_chat_complete", side_effect=llm_ok), \
             patch("agent.nodes.get_embedding", return_value=[0.0] * 3072), \
             patch("agent.nodes.search_vector", return_value=fake_docs):
            config = {"configurable": {"thread_id": thread}}
            graph.invoke({"query": "first query", "transformation_count": 0}, config=config)

        # Verify state is checkpointed
        state_snapshot = graph.get_state({"configurable": {"thread_id": thread}})
        assert state_snapshot is not None
        assert state_snapshot.values.get("generation") == "First answer."
        assert len(state_snapshot.values.get("follow_up_questions", [])) == 3

    def test_final_state_schema(self):
        checkpointer = MemorySaver()
        graph = build_legal_graph(checkpointer=checkpointer)
        fake_docs = _fake_docs(2)

        def llm_ok(messages, **kwargs):
            system = messages[0]["content"] if messages else ""
            if "optimizer" in system:
                return "rewritten"
            if "grader" in system:
                return "0.8"
            if "precise" in system:
                return "Answer [Doc 1]."
            if "research assistant" in system:
                return "1. Q1\n2. Q2\n3. Q3"
            return "ok"

        with patch("agent.nodes.openai_chat_complete", side_effect=llm_ok), \
             patch("agent.nodes.get_embedding", return_value=[0.0] * 3072), \
             patch("agent.nodes.search_vector", return_value=fake_docs):
            config = {"configurable": {"thread_id": "test-schema"}}
            result = graph.invoke(
                {"query": "schema test", "transformation_count": 0},
                config=config,
            )

        assert "generation" in result
        assert "source_documents" in result
        assert "follow_up_questions" in result
        assert isinstance(result["generation"], str)
        assert isinstance(result["source_documents"], list)
        assert isinstance(result["follow_up_questions"], list)
