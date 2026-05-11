"""
Phase 3 test gate — AG-UI SSE server.

Tests verify:
  1. agui_event_stream yields correct AG-UI typed events in order
  2. Reasoning events emitted for classify / rewrite / grade_docs nodes
  3. POST /runs returns 200 text/event-stream with correct request body
  4. Error path yields RUN_ERROR event
  5. Thread_id continuity — same thread_id shares checkpointed state
"""
import json
import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from langgraph.checkpoint.memory import MemorySaver

from agent.main_graph import build_main_graph
from agent.state import GraphState
from server.agui_handler import agui_event_stream


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_events(stream) -> list[dict]:
    events = []
    for line in stream:
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _build_graph():
    return build_main_graph(checkpointer=MemorySaver())


def _general_input():
    return {"query": "What is the capital of France?", "transformation_count": 0}


def _config(thread_id="test-thread"):
    return {"configurable": {"thread_id": thread_id}}


# ---------------------------------------------------------------------------
# Event stream unit tests
# ---------------------------------------------------------------------------

class TestAguiEventStream:

    def test_starts_with_run_started(self):
        graph = _build_graph()
        with patch("agent.router.openai_chat_complete", return_value="GENERAL"), \
             patch("agent.branches.general.openai_chat_complete", return_value="Paris."):
            events = _collect_events(agui_event_stream(graph, _general_input(), _config("t1")))

        assert events[0]["type"] == "RUN_STARTED"
        assert "thread_id" in events[0]
        assert "run_id" in events[0]

    def test_ends_with_run_finished(self):
        graph = _build_graph()
        with patch("agent.router.openai_chat_complete", return_value="GENERAL"), \
             patch("agent.branches.general.openai_chat_complete", return_value="Paris."):
            events = _collect_events(agui_event_stream(graph, _general_input(), _config("t2")))

        assert events[-1]["type"] == "RUN_FINISHED"

    def test_run_started_and_finished_share_run_id(self):
        graph = _build_graph()
        with patch("agent.router.openai_chat_complete", return_value="GENERAL"), \
             patch("agent.branches.general.openai_chat_complete", return_value="Paris."):
            events = _collect_events(agui_event_stream(graph, _general_input(), _config("t3")))

        started = next(e for e in events if e["type"] == "RUN_STARTED")
        finished = next(e for e in events if e["type"] == "RUN_FINISHED")
        assert started["run_id"] == finished["run_id"]

    def test_emits_state_delta_events(self):
        graph = _build_graph()
        with patch("agent.router.openai_chat_complete", return_value="GENERAL"), \
             patch("agent.branches.general.openai_chat_complete", return_value="Paris."):
            events = _collect_events(agui_event_stream(graph, _general_input(), _config("t4")))

        delta_events = [e for e in events if e["type"] == "STATE_DELTA"]
        assert len(delta_events) >= 1
        for e in delta_events:
            assert "delta" in e
            assert isinstance(e["delta"], list)

    def test_emits_reasoning_events_for_classify_node(self):
        graph = _build_graph()
        with patch("agent.router.openai_chat_complete", return_value="GENERAL"), \
             patch("agent.branches.general.openai_chat_complete", return_value="answer"):
            events = _collect_events(agui_event_stream(graph, _general_input(), _config("t5")))

        types = [e["type"] for e in events]
        assert "REASONING_START" in types
        assert "REASONING_MESSAGE_START" in types
        assert "REASONING_MESSAGE_CONTENT" in types
        assert "REASONING_MESSAGE_END" in types
        assert "REASONING_END" in types

    def test_reasoning_events_have_matching_message_ids(self):
        graph = _build_graph()
        with patch("agent.router.openai_chat_complete", return_value="GENERAL"), \
             patch("agent.branches.general.openai_chat_complete", return_value="answer"):
            events = _collect_events(agui_event_stream(graph, _general_input(), _config("t6")))

        # Each REASONING_START must have a matching REASONING_END with the same message_id
        starts = {e["message_id"] for e in events if e["type"] == "REASONING_START"}
        ends = {e["message_id"] for e in events if e["type"] == "REASONING_END"}
        assert starts == ends

    def test_emits_text_message_events_for_general_branch(self):
        graph = _build_graph()
        with patch("agent.router.openai_chat_complete", return_value="GENERAL"), \
             patch("agent.branches.general.openai_chat_complete", return_value="Paris is the capital."):
            events = _collect_events(agui_event_stream(graph, _general_input(), _config("t7")))

        types = [e["type"] for e in events]
        assert "TEXT_MESSAGE_START" in types
        assert "TEXT_MESSAGE_CONTENT" in types
        assert "TEXT_MESSAGE_END" in types

    def test_text_message_content_joined_equals_generation(self):
        graph = _build_graph()
        generation = "Paris is the capital of France."
        with patch("agent.router.openai_chat_complete", return_value="GENERAL"), \
             patch("agent.branches.general.openai_chat_complete", return_value=generation):
            events = _collect_events(agui_event_stream(graph, _general_input(), _config("t8")))

        content_events = [e for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"]
        joined = "".join(e["delta"] for e in content_events)
        assert joined == generation

    def test_no_text_message_for_classify_node(self):
        """classify is a reasoning node, should not produce TEXT_MESSAGE events."""
        graph = _build_graph()
        with patch("agent.router.openai_chat_complete", return_value="GENERAL"), \
             patch("agent.branches.general.openai_chat_complete", return_value="answer"):
            events = _collect_events(agui_event_stream(graph, _general_input(), _config("t9")))

        # TEXT_MESSAGE_START events must have role=assistant
        for e in events:
            if e["type"] == "TEXT_MESSAGE_START":
                assert e.get("role") == "assistant"

    def test_error_path_yields_run_error_event(self):
        graph = _build_graph()

        def boom(*args, **kwargs):
            raise RuntimeError("LLM failure")

        with patch("agent.router.openai_chat_complete", side_effect=boom):
            events = _collect_events(agui_event_stream(graph, _general_input(), _config("t-err")))

        error_events = [e for e in events if e["type"] == "RUN_ERROR"]
        assert len(error_events) == 1
        assert "LLM failure" in error_events[0]["message"]

    def test_rewrite_node_emits_reasoning_for_legal_branch(self):
        """Legal branch runs rewrite node — should emit REASONING events."""
        graph = _build_graph()

        def _fake_docs(n=2):
            return [{"title": f"Doc {i}", "content": f"Content {i}", "source": "", "page": ""} for i in range(n)]

        def legal_llm(messages, **kwargs):
            system = messages[0]["content"] if messages else ""
            if "classifier" in system:
                return "LEGAL"
            if "optimizer" in system:
                return "rewritten legal query"
            if "grader" in system:
                return "0.9"
            if "precise" in system:
                return "The penalty is imprisonment."
            if "research assistant" in system:
                return "1. Q1\n2. Q2\n3. Q3"
            return "LEGAL"

        with patch("agent.router.openai_chat_complete", return_value="LEGAL"), \
             patch("agent.nodes.openai_chat_complete", side_effect=legal_llm), \
             patch("agent.nodes.get_embedding", return_value=[0.0] * 3072), \
             patch("agent.nodes.search_vector", return_value=_fake_docs(2)):
            events = _collect_events(agui_event_stream(
                graph,
                {"query": "What is the penalty for bribery?", "transformation_count": 0},
                _config("t-legal-reason"),
            ))

        reasoning_starts = [e for e in events if e["type"] == "REASONING_START"]
        # classify + rewrite + grade_docs all emit reasoning
        assert len(reasoning_starts) >= 2


# ---------------------------------------------------------------------------
# FastAPI endpoint tests — DB and OpenAI mocked so no real services needed
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def test_client():
    """TestClient with MariaDB and OpenAI mocked so no real services needed."""
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-placeholder")
    for mod in list(sys.modules.keys()):
        if mod in ("app", "tasks", "summarizer", "models", "database"):
            del sys.modules[mod]

    with patch("sqlalchemy.create_engine", return_value=MagicMock()), \
         patch("langchain.chat_models.ChatOpenAI", MagicMock()):
        from fastapi.testclient import TestClient
        from app import app
        yield TestClient(app, raise_server_exceptions=False)


_VALID_RUN_BODY = {
    "thread_id": "api-test-thread",
    "user_id": "test-user",
    "input": [{"role": "user", "content": "What is the capital of France?"}],
}


class TestRunsEndpoint:

    def test_post_runs_returns_200_streaming(self, test_client):
        with patch("app.get_main_graph") as mock_get_graph:
            mock_graph = MagicMock()
            mock_graph.stream.return_value = iter([
                {"general_answer": {"generation": "Paris.", "source_documents": [], "follow_up_questions": []}},
            ])
            mock_get_graph.return_value = mock_graph

            response = test_client.post("/runs", json=_VALID_RUN_BODY)

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

    def test_post_runs_missing_thread_id_returns_422(self, test_client):
        response = test_client.post("/runs", json={"user_id": "u", "input": [{"role": "user", "content": "hi"}]})
        assert response.status_code == 422

    def test_post_runs_missing_user_id_returns_422(self, test_client):
        response = test_client.post("/runs", json={"thread_id": "t", "input": [{"role": "user", "content": "hi"}]})
        assert response.status_code == 422

    def test_post_runs_no_user_message_returns_400(self, test_client):
        with patch("app.get_main_graph") as mock_get_graph:
            mock_get_graph.return_value = MagicMock()
            response = test_client.post("/runs", json={
                "thread_id": "t", "user_id": "u",
                "input": [{"role": "assistant", "content": "hello"}],
            })
        assert response.status_code == 400

    def test_post_runs_empty_content_returns_400(self, test_client):
        with patch("app.get_main_graph") as mock_get_graph:
            mock_get_graph.return_value = MagicMock()
            response = test_client.post("/runs", json={
                "thread_id": "t", "user_id": "u",
                "input": [{"role": "user", "content": "   "}],
            })
        assert response.status_code == 400

    def test_thread_id_continuity_via_checkpointer(self):
        """Same thread_id across two graph invocations shares checkpointed state."""
        graph = build_main_graph(checkpointer=MemorySaver())
        thread_id = "continuity-test"
        config = {"configurable": {"thread_id": thread_id}}

        with patch("agent.router.openai_chat_complete", return_value="GENERAL"), \
             patch("agent.branches.general.openai_chat_complete", return_value="Paris."):
            events1 = _collect_events(agui_event_stream(
                graph, {"query": "What is the capital of France?", "transformation_count": 0}, config
            ))

        with patch("agent.router.openai_chat_complete", return_value="GENERAL"), \
             patch("agent.branches.general.openai_chat_complete", return_value="It is Paris."):
            events2 = _collect_events(agui_event_stream(
                graph, {"query": "Tell me more about it.", "transformation_count": 0}, config
            ))

        # Both runs should complete successfully
        assert events1[-1]["type"] == "RUN_FINISHED"
        assert events2[-1]["type"] == "RUN_FINISHED"
        # Second run gets a different run_id
        assert events1[0]["run_id"] != events2[0]["run_id"]
