"""
AG-UI event emitter using the official ag-ui-protocol EventEncoder.

Event sequence per request:
  RUN_STARTED
  per node:
    STEP_STARTED(step_name)
    STATE_SNAPSHOT                             (always)
    REASONING_START / CONTENT×N / END          (reasoning nodes: classify, rewrite, grade_docs)
    TEXT_MESSAGE_START / CONTENT×N / END       (answer nodes: generate, fallback, *_answer)
    STEP_FINISHED(step_name)
  RUN_FINISHED  (or RUN_ERROR on exception)
"""
import uuid
import logging
from typing import Iterator

from ag_ui.core import (
    EventType,
    RunStartedEvent,
    RunFinishedEvent,
    RunErrorEvent,
    StateSnapshotEvent,
    StepStartedEvent,
    StepFinishedEvent,
    TextMessageStartEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    ReasoningStartEvent,
    ReasoningMessageStartEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningEndEvent,
)
from ag_ui.encoder import EventEncoder

logger = logging.getLogger(__name__)

_encoder = EventEncoder()

# Nodes whose LLM output is a REASONING step (classification / query planning / grading)
_REASONING_NODES = {"classify", "rewrite", "grade_docs"}

# Nodes whose LLM output is the final TEXT answer streamed to the user
_TEXT_NODES = {"generate", "fallback", "general_answer", "web_search_answer", "calculation_answer"}

# Human-readable label shown in the step indicator
_NODE_LABELS = {
    "classify":           "Classifying query",
    "rewrite":            "Rewriting query",
    "retrieve":           "Retrieving documents",
    "grade_docs":         "Grading relevance",
    "generate":           "Generating answer",
    "fallback":           "Generating fallback answer",
    "follow_up":          "Generating follow-up questions",
    "general_answer":     "Answering",
    "calculation_answer": "Calculating penalty",
    "web_search_answer":  "Searching the web",
}


def _sse(event) -> str:
    return _encoder.encode(event)


def _word_chunks(text: str):
    words = text.split(" ")
    for i, word in enumerate(words):
        yield word if i == len(words) - 1 else word + " "


def agui_event_stream(graph, initial_state: dict, config: dict) -> Iterator[str]:
    """
    Run graph.stream() in 'updates' mode and yield AG-UI SSE strings.
    Yields plain strings — caller wraps in FastAPI StreamingResponse.
    """
    run_id = str(uuid.uuid4())
    thread_id = config.get("configurable", {}).get("thread_id", run_id)
    outcome = "success"

    logger.info("AG-UI run started — run_id=%s thread_id=%s", run_id, thread_id)
    yield _sse(RunStartedEvent(type=EventType.RUN_STARTED, thread_id=thread_id, run_id=run_id))

    current_state: dict = {}

    try:
        for chunk in graph.stream(initial_state, config=config, stream_mode="updates"):
            for node_name, node_output in chunk.items():
                label = _NODE_LABELS.get(node_name, node_name)
                logger.info("AG-UI node=%s", node_name)

                # STEP_STARTED
                yield _sse(StepStartedEvent(type=EventType.STEP_STARTED, step_name=node_name))

                # Merge node output into running state and emit STATE_SNAPSHOT
                current_state.update(node_output)
                yield _sse(StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=current_state))

                # REASONING events — for nodes that do CoT / classification / grading
                if node_name in _REASONING_NODES:
                    if node_name == "rewrite":
                        reasoning_text = f"[{label}] Rewritten query: {node_output.get('query', '')}"
                    elif node_name == "grade_docs":
                        reasoning_text = f"[{label}] Relevance score: {node_output.get('_grade_avg', 0.0):.3f}"
                    elif node_name == "classify":
                        reasoning_text = f"[{label}] Routing to: {node_output.get('branch', 'legal')}"
                    else:
                        reasoning_text = f"[{label}]"

                    msg_id = str(uuid.uuid4())
                    yield _sse(ReasoningStartEvent(type=EventType.REASONING_START, message_id=msg_id))
                    yield _sse(ReasoningMessageStartEvent(type=EventType.REASONING_MESSAGE_START, message_id=msg_id, role="reasoning"))
                    for word_chunk in _word_chunks(reasoning_text):
                        yield _sse(ReasoningMessageContentEvent(type=EventType.REASONING_MESSAGE_CONTENT, message_id=msg_id, delta=word_chunk))
                    yield _sse(ReasoningMessageEndEvent(type=EventType.REASONING_MESSAGE_END, message_id=msg_id))
                    yield _sse(ReasoningEndEvent(type=EventType.REASONING_END, message_id=msg_id))

                # TEXT_MESSAGE events — for nodes that produce the final answer
                if node_name in _TEXT_NODES:
                    generation = node_output.get("generation", "")
                    if generation:
                        msg_id = str(uuid.uuid4())
                        yield _sse(TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=msg_id, role="assistant"))
                        for word_chunk in _word_chunks(generation):
                            yield _sse(TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id=msg_id, delta=word_chunk))
                        yield _sse(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=msg_id))

                if node_name == "fallback":
                    outcome = "fallback"

                # STEP_FINISHED
                yield _sse(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=node_name))

    except Exception as exc:
        logger.exception("AG-UI run failed — run_id=%s", run_id)
        yield _sse(RunErrorEvent(type=EventType.RUN_ERROR, message=str(exc), code="GRAPH_ERROR"))
        return

    yield _sse(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=thread_id, run_id=run_id))
    logger.info("AG-UI run finished — run_id=%s outcome=%s", run_id, outcome)
