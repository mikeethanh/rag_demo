"""
Phase 2 test gate — All branches + main graph end-to-end.

All LLM, Qdrant, and Tavily calls are mocked.
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from langgraph.checkpoint.memory import MemorySaver

from agent.state import GraphState
from agent.branches.general import general_answer
from agent.branches.calculation import calculation_answer
from agent.branches.web_search import web_search_answer
from agent.main_graph import build_main_graph


def _base_state(query: str = "test query") -> GraphState:
    return GraphState(
        query=query,
        documents=[],
        generation="",
        transformation_count=0,
        follow_up_questions=[],
        source_documents=[],
    )


def _fake_docs(n=2):
    return [{"title": f"Doc {i}", "content": f"Content {i}", "source": "", "page": ""} for i in range(n)]


# ---------------------------------------------------------------------------
# General branch
# ---------------------------------------------------------------------------

class TestGeneralBranch:
    def test_returns_generation(self):
        state = _base_state("What is the capital of France?")
        with patch("agent.branches.general.openai_chat_complete", return_value="Paris."):
            result = general_answer(state)
        assert result["generation"] == "Paris."

    def test_clears_source_documents(self):
        state = _base_state()
        state["source_documents"] = [{"title": "x"}]
        with patch("agent.branches.general.openai_chat_complete", return_value="answer"):
            result = general_answer(state)
        assert result["source_documents"] == []

    def test_clears_follow_up_questions(self):
        state = _base_state()
        with patch("agent.branches.general.openai_chat_complete", return_value="answer"):
            result = general_answer(state)
        assert result["follow_up_questions"] == []


# ---------------------------------------------------------------------------
# Calculation branch
# ---------------------------------------------------------------------------

class TestCalculationBranch:
    def _make_tool_call_message(self, tool_name, args_dict, call_id="call_1"):
        """Build a mock raw message that has tool_calls set."""
        tc = MagicMock()
        tc.id = call_id
        tc.function.name = tool_name
        tc.function.arguments = json.dumps(args_dict)

        msg = MagicMock()
        msg.tool_calls = [tc]
        msg.content = None
        return msg

    def _make_text_message(self, text):
        msg = MagicMock()
        msg.tool_calls = None
        msg.content = text
        return msg

    def test_calls_penalty_calculator_tool(self):
        """LLM first asks for penalty_calculator, then returns text."""
        state = _base_state("Calculate penalty for tax evasion of 100000000 VND")

        call_seq = [
            self._make_tool_call_message("penalty_calculator", {"offense_type": "tax_evasion", "base_amount": 100_000_000}),
            self._make_text_message("The base penalty is 20,000,000 VND."),
        ]
        with patch("agent.branches.calculation.openai_chat_complete", side_effect=call_seq):
            result = calculation_answer(state)

        assert "penalty" in result["generation"].lower() or "20" in result["generation"]
        assert result["source_documents"] == []

    def test_calls_both_tools_in_sequence(self):
        """LLM calls penalty_calculator then apply_factors before final answer."""
        state = _base_state("Fine for bribery with first offense mitigation")

        call_seq = [
            self._make_tool_call_message("penalty_calculator", {"offense_type": "bribery", "base_amount": 50_000_000}),
            self._make_tool_call_message("apply_factors", {"base_penalty": 10_000_000, "mitigating_factors": ["first_offense"]}),
            self._make_text_message("After mitigation, the final penalty is 9,000,000 VND."),
        ]
        with patch("agent.branches.calculation.openai_chat_complete", side_effect=call_seq):
            result = calculation_answer(state)

        assert result["generation"] != ""
        assert result["source_documents"] == []

    def test_stub_penalty_calculator_returns_20_percent(self):
        """Verify stub math: 20% of amount, minimum 5M."""
        from agent.branches.calculation import _run_tool
        out = json.loads(_run_tool("penalty_calculator", {"offense_type": "fraud", "base_amount": 100_000_000}))
        assert out["base_penalty"] == 20_000_000.0

    def test_stub_penalty_calculator_minimum(self):
        """Verify 5M minimum for tiny amounts."""
        from agent.branches.calculation import _run_tool
        out = json.loads(_run_tool("penalty_calculator", {"offense_type": "fraud", "base_amount": 1_000}))
        assert out["base_penalty"] == 5_000_000.0

    def test_stub_apply_factors_first_offense(self):
        """first_offense gives 10% reduction."""
        from agent.branches.calculation import _run_tool
        out = json.loads(_run_tool("apply_factors", {"base_penalty": 10_000_000, "mitigating_factors": ["first_offense"]}))
        assert abs(out["final_penalty"] - 9_000_000.0) < 1.0

    def test_fallback_when_loop_exhausted(self):
        """If LLM keeps returning tool calls and never gives text, return fallback message."""
        state = _base_state("query")

        def always_tool(messages, **kwargs):
            tc = MagicMock()
            tc.id = "call_x"
            tc.function.name = "penalty_calculator"
            tc.function.arguments = json.dumps({"offense_type": "x", "base_amount": 1})
            msg = MagicMock()
            msg.tool_calls = [tc]
            msg.content = None
            return msg

        with patch("agent.branches.calculation.openai_chat_complete", side_effect=always_tool):
            result = calculation_answer(state)

        assert "Unable to calculate" in result["generation"]


# ---------------------------------------------------------------------------
# Web Search branch
# ---------------------------------------------------------------------------

class TestWebSearchBranch:
    def _fake_tavily_results(self):
        return [
            {"title": "Tax law overview", "url": "https://example.com/tax", "content": "Tax evasion is punishable..."},
            {"title": "Penalty guide", "url": "https://example.com/penalty", "content": "Fines range from..."},
        ]

    def test_returns_generation_and_sources(self):
        state = _base_state("current tax evasion penalty in Vietnam 2024")
        with patch("agent.branches.web_search.openai_chat_complete", return_value="The penalty is..."), \
             patch("agent.branches.web_search._tavily_search", return_value=self._fake_tavily_results()):
            result = web_search_answer(state)

        assert result["generation"] != ""
        assert len(result["source_documents"]) == 2
        assert result["source_documents"][0]["source"] == "https://example.com/tax"

    def test_source_documents_have_correct_schema(self):
        state = _base_state("query")
        with patch("agent.branches.web_search.openai_chat_complete", return_value="answer"), \
             patch("agent.branches.web_search._tavily_search", return_value=self._fake_tavily_results()):
            result = web_search_answer(state)

        for doc in result["source_documents"]:
            assert "title" in doc
            assert "source" in doc
            assert "page" in doc

    def test_empty_results_returns_fallback_message(self):
        state = _base_state("very obscure query")
        with patch("agent.branches.web_search.openai_chat_complete", return_value="rewritten"), \
             patch("agent.branches.web_search._tavily_search", return_value=[]):
            result = web_search_answer(state)

        assert "No web search results" in result["generation"]
        assert result["source_documents"] == []

    def test_clears_follow_up_questions(self):
        state = _base_state("query")
        with patch("agent.branches.web_search.openai_chat_complete", return_value="answer"), \
             patch("agent.branches.web_search._tavily_search", return_value=self._fake_tavily_results()):
            result = web_search_answer(state)
        assert result["follow_up_questions"] == []


# ---------------------------------------------------------------------------
# Aggregator schema — all branches normalize to the same output shape
# ---------------------------------------------------------------------------

class TestAggregatorSchema:
    REQUIRED_KEYS = {"generation", "source_documents", "follow_up_questions"}

    def _assert_schema(self, result):
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key}"
        assert isinstance(result["generation"], str)
        assert isinstance(result["source_documents"], list)
        assert isinstance(result["follow_up_questions"], list)

    def test_general_branch_schema(self):
        state = _base_state("hello")
        with patch("agent.branches.general.openai_chat_complete", return_value="Hi there."):
            self._assert_schema(general_answer(state))

    def test_web_search_branch_schema(self):
        state = _base_state("query")
        fake_results = [{"title": "t", "url": "https://x.com", "content": "c"}]
        with patch("agent.branches.web_search.openai_chat_complete", return_value="answer"), \
             patch("agent.branches.web_search._tavily_search", return_value=fake_results):
            self._assert_schema(web_search_answer(state))

    def test_calculation_branch_schema(self):
        state = _base_state("calculate fine")
        msg = MagicMock()
        msg.tool_calls = None
        msg.content = "The fine is 5,000,000 VND."
        with patch("agent.branches.calculation.openai_chat_complete", return_value=msg):
            self._assert_schema(calculation_answer(state))


# ---------------------------------------------------------------------------
# Main graph end-to-end — one test per branch route
# ---------------------------------------------------------------------------

class TestMainGraphEndToEnd:
    def _invoke(self, graph, query, router_output, llm_side_effect, extra_patches=None):
        patches = [
            patch("agent.router.openai_chat_complete", return_value=router_output),
            patch("agent.nodes.get_embedding", return_value=[0.0] * 3072),
            patch("agent.nodes.search_vector", return_value=_fake_docs(2)),
        ]
        if extra_patches:
            patches.extend(extra_patches)

        config = {"configurable": {"thread_id": f"test-{router_output.lower()}"}}
        with patches[0], patches[1], patches[2]:
            if extra_patches:
                with extra_patches[0]:
                    result = graph.invoke({"query": query, "transformation_count": 0}, config=config)
            else:
                with patch(llm_side_effect[0], side_effect=llm_side_effect[1]):
                    result = graph.invoke({"query": query, "transformation_count": 0}, config=config)
        return result

    def test_routes_legal_query_through_legal_graph(self):
        graph = build_main_graph(checkpointer=MemorySaver())
        fake_docs = _fake_docs(2)

        def legal_llm(messages, **kwargs):
            system = messages[0]["content"] if messages else ""
            if "optimizer" in system:
                return "rewritten legal query"
            if "grader" in system:
                return "0.9"
            if "precise" in system:
                return "The penalty is imprisonment [Doc 1]."
            if "research assistant" in system:
                return "1. Q1\n2. Q2\n3. Q3"
            return "LEGAL"

        with patch("agent.router.openai_chat_complete", return_value="LEGAL"), \
             patch("agent.nodes.openai_chat_complete", side_effect=legal_llm), \
             patch("agent.nodes.get_embedding", return_value=[0.0] * 3072), \
             patch("agent.nodes.search_vector", return_value=fake_docs):
            config = {"configurable": {"thread_id": "e2e-legal"}}
            result = graph.invoke({"query": "What is the penalty for bribery?", "transformation_count": 0}, config=config)

        assert result["branch"] == "legal"
        assert result["generation"] != ""
        assert len(result["follow_up_questions"]) == 3

    def test_routes_general_query(self):
        graph = build_main_graph(checkpointer=MemorySaver())

        with patch("agent.router.openai_chat_complete", return_value="GENERAL"), \
             patch("agent.branches.general.openai_chat_complete", return_value="Paris is the capital of France."):
            config = {"configurable": {"thread_id": "e2e-general"}}
            result = graph.invoke({"query": "What is the capital of France?", "transformation_count": 0}, config=config)

        assert result["branch"] == "general"
        assert "Paris" in result["generation"]
        assert result["source_documents"] == []

    def test_routes_calculation_query(self):
        graph = build_main_graph(checkpointer=MemorySaver())

        final_msg = MagicMock()
        final_msg.tool_calls = None
        final_msg.content = "The calculated penalty is 20,000,000 VND."

        with patch("agent.router.openai_chat_complete", return_value="CALCULATION"), \
             patch("agent.branches.calculation.openai_chat_complete", return_value=final_msg):
            config = {"configurable": {"thread_id": "e2e-calc"}}
            result = graph.invoke({"query": "Calculate the fine for 100M VND tax evasion", "transformation_count": 0}, config=config)

        assert result["branch"] == "calculation"
        assert result["generation"] != ""

    def test_routes_ambiguous_query_to_web_search(self):
        graph = build_main_graph(checkpointer=MemorySaver())
        fake_results = [{"title": "News", "url": "https://news.com", "content": "Recent update..."}]

        with patch("agent.router.openai_chat_complete", return_value="AMBIGUOUS"), \
             patch("agent.branches.web_search.openai_chat_complete", return_value="Here is what the web says."), \
             patch("agent.branches.web_search._tavily_search", return_value=fake_results):
            config = {"configurable": {"thread_id": "e2e-web"}}
            result = graph.invoke({"query": "latest tax reform news Vietnam 2024", "transformation_count": 0}, config=config)

        assert result["branch"] == "web_search"
        assert result["generation"] != ""
        assert len(result["source_documents"]) == 1

    def test_final_state_has_branch_field(self):
        """All routes must include 'branch' in the final state."""
        graph = build_main_graph(checkpointer=MemorySaver())

        with patch("agent.router.openai_chat_complete", return_value="GENERAL"), \
             patch("agent.branches.general.openai_chat_complete", return_value="answer"):
            config = {"configurable": {"thread_id": "e2e-branch-field"}}
            result = graph.invoke({"query": "hello", "transformation_count": 0}, config=config)

        assert "branch" in result
        assert result["branch"] == "general"
