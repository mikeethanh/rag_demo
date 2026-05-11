# Phases 1–3 Overview — LangGraph + AG-UI Migration

This document explains what each of the first three migration phases does, why it exists, and what it produces. Read this before starting or reviewing any phase.

---

## Why three phases before the UI?

The migration replaces three fundamentally different systems:
1. **The reasoning engine** — Celery tasks → LangGraph graph (Phase 1–2)
2. **The transport layer** — polling REST → SSE streaming (Phase 3)
3. **The frontend** — Streamlit → Next.js (Phase 4)

These are separated into phases because each layer can be built and tested independently. Phase 1 has no server. Phase 2 has no streaming. Phase 3 has no new UI. Each phase is a working, testable increment.

---

## Phase 1 — LangGraph Legal RAG Core

### What it is

The core reasoning engine for legal document queries. Phase 1 takes the existing linear Celery pipeline (embed → search → generate) and replaces it with a LangGraph `StateGraph` that can loop, self-evaluate, and retry.

### What problem it solves

The old pipeline always returned an answer, even if the retrieved documents were irrelevant to the query. There was no self-correction — a bad retrieval produced a bad (but confident) answer.

Phase 1 adds a **self-reflection loop**: after retrieving documents, a grading node scores each one for relevance. If the average relevance score is below 0.7, the query is rewritten and retrieval runs again. This repeats up to 3 times before the graph gives up and falls back to a citation-free answer.

### Graph structure

```
START → rewrite → retrieve → grade_docs
                                ├── score >= 0.7          → generate → follow_up → END
                                ├── score < 0.7, count < 3 → rewrite  (loop)
                                └── score < 0.7, count >= 3 → fallback → END
```

### Files produced

| File | What it contains |
|---|---|
| `backend/src/agent/state.py` | `GraphState` TypedDict — the shared state object all nodes read and write |
| `backend/src/agent/nodes.py` | All 6 node functions (`rewrite`, `retrieve`, `grade_docs`, `generate`, `fallback`, `follow_up`) + `rollback_router` conditional edge |
| `backend/src/agent/legal_graph.py` | Graph assembly — wires nodes and edges, exposes `build_legal_graph()` factory and `get_legal_graph()` singleton |
| `backend/src/agent/checkpointer.py` | SQLite checkpointer factory — persists `GraphState` between requests so the same `thread_id` remembers conversation history |
| `backend/src/agent/__init__.py` | Package marker |
| `backend/src/agent/branches/__init__.py` | Package marker (pre-creates `branches/` sub-package for Phase 2) |
| `backend/tests/conftest.py` | Adds `src/` to `sys.path` so tests can import source modules without installing the package |
| `backend/tests/test_legal_graph.py` | 18 tests — routing logic, each node in isolation, and 5 end-to-end graph invocations |

### What it does NOT do

- Phase 1 does **not** expose any HTTP endpoint. There is no server change.
- Phase 1 does **not** touch `app.py`, `tasks.py`, or any Celery code.
- Phase 1 does **not** add the semantic router. Every query goes straight into the legal graph.
- Phase 1 does **not** wire into the running system. The new `agent/` package exists alongside the old code and is not called by anything yet.

### Test gate

```bash
cd backend
python -m pytest tests/test_legal_graph.py -v
# Expected: 18 passed
```

---

## Phase 2 — Semantic Router + All Branches

### What it is

A classifier node that sits in front of all reasoning branches, plus implementations of the three non-legal branches.

### What problem it solves

The legal graph only handles legal queries. A real chatbot receives general questions, calculation requests, and ambiguous queries that need web search. Phase 2 adds a routing layer that inspects every incoming query and directs it to the right handler before any retrieval or generation runs.

### How the router works

A single LLM call classifies the query into one of four categories:

```
LEGAL       → legal_graph (built in Phase 1)
GENERAL     → direct gpt-4o-mini call, no retrieval
CALCULATION → stub penalty calculator tools (placeholder)
AMBIGUOUS   → Tavily web search agent
```

The router is a LangGraph node that runs first in a new `main_graph`. Its output is a conditional edge that branches to the correct sub-graph.

### Branch implementations

**General branch** (`branches/general.py`)
- Single LLM call with a system prompt. No retrieval.
- Normalizes output to `{generation, source_documents: [], follow_up_questions: []}`.

**Calculation branch** (`branches/calculation.py`) — stub
- Defines two placeholder tools: `penalty_calculator(offense_type, base_amount)` and `apply_factors(base_penalty, mitigating_factors)`.
- Tools return hardcoded values with realistic field names — no real math.
- LLM calls tools via OpenAI function calling and formats the result into a sentence.
- This is a stub interface; real penalty logic is out of scope.

**Web Search branch** (`branches/web_search.py`)
- Three nodes: query rewriter → Tavily search (top-5 results) → result summarizer.
- Requires `TAVILY_API_KEY` environment variable.
- Returns `{generation, source_documents: [url, ...], follow_up_questions: []}`.

### Response aggregator

Every branch normalizes output to the same schema:

```python
{
    "generation": str,
    "source_documents": list,
    "follow_up_questions": list,
    "branch": str,   # "legal" | "general" | "calculation" | "web_search"
}
```

The `branch` field tells the UI which icon/label to show.

### Files produced

| File | What it contains |
|---|---|
| `backend/src/agent/router.py` | `SemanticRouter` node — LLM classifier, conditional edge logic |
| `backend/src/agent/main_graph.py` | Top-level `StateGraph` — router + all 4 branch sub-graphs wired together |
| `backend/src/agent/branches/general.py` | Direct LLM branch |
| `backend/src/agent/branches/calculation.py` | Stub tool-calling branch |
| `backend/src/agent/branches/web_search.py` | Tavily search branch |
| `backend/tests/test_router.py` | Router classification tests (mock LLM) |
| `backend/tests/test_branches.py` | Per-branch node and end-to-end tests (mock LLM + mock Tavily) |

### What it does NOT do

- Phase 2 still has **no server endpoint**. `main_graph` is importable but not called by `app.py` yet.
- Phase 2 does **not** remove Celery. Old code is still running.

### Test gate

```bash
cd backend
python -m pytest tests/test_router.py tests/test_branches.py -v
```

---

## Phase 3 — AG-UI Server (FastAPI `/runs` SSE Endpoint)

### What it is

The transport layer that connects the LangGraph graph to the outside world via Server-Sent Events using the AG-UI protocol.

### What problem it solves

The old API used a polling pattern: `POST /chat/complete` returned a `task_id`, and the client polled `GET /chat/complete/{task_id}` until the task finished. The user saw nothing until the entire response was ready.

Phase 3 replaces polling with streaming. The client sends one request and receives a continuous stream of typed events as each LangGraph node runs. The UI can update in real time — showing the current step, streaming reasoning tokens, and streaming the final answer word by word.

### AG-UI event sequence

```
RUN_STARTED                → run started, thread_id echoed back
STATE_DELTA                → RFC-6902 JSON patch of GraphState after each node
REASONING_START            → a node with CoT reasoning has started
REASONING_MESSAGE_CONTENT  → streamed reasoning tokens (one per chunk)
REASONING_END              → reasoning phase complete
TEXT_MESSAGE_START         → the final answer is starting to stream
TEXT_MESSAGE_CONTENT       → streamed answer tokens (one per chunk)
TEXT_MESSAGE_END           → answer complete
RUN_FINISHED               → run complete, outcome: "success" | "fallback"
```

### API contract

```
POST /runs
Content-Type: application/json

{
  "thread_id": "string",
  "user_id": "string",
  "input": [{ "role": "user", "content": "What is the penalty for bribery?" }],
  "state": {}
}
```

Response: `Content-Type: text/event-stream`

### What gets removed in Phase 3

| Removed | Why |
|---|---|
| `POST /chat/complete` (submit) | Replaced by `POST /runs` |
| `GET /chat/complete/{task_id}` (poll) | Replaced by SSE stream |
| Celery task dispatch in `app.py` | LangGraph runs inline in the request |
| `tasks.py` Celery tasks | Replaced by LangGraph nodes |
| `valkey` Docker service | No broker needed without Celery |
| `celery-worker` Docker service | Removed entirely |
| MariaDB conversation tables | Replaced by LangGraph checkpointer |

### What stays

| Kept | Why |
|---|---|
| `POST /document/create` | Document indexing is unchanged |
| `POST /collection/create` | Collection management is unchanged |
| `vectorize.py` | Qdrant operations unchanged |
| `models.py` Document model | Document storage still uses MariaDB |
| MariaDB `document` table | Documents not chat history |

### Files produced

| File | What it contains |
|---|---|
| `backend/src/server/agui_handler.py` | AG-UI event emitter — wraps LangGraph streaming output into typed SSE events |
| `backend/src/app.py` | Modified — adds `POST /runs`, removes polling endpoints, removes Celery dispatch |
| `backend/tests/test_agui_server.py` | SSE endpoint tests — event ordering, RFC-6902 patch validity, streaming behavior, memory continuity |

### Test gate

```bash
cd backend
python -m pytest tests/test_agui_server.py -v
# Then manual smoke:
curl -N -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"t1","user_id":"u1","input":[{"role":"user","content":"What is the penalty for tax evasion?"}]}'
```

---

## How the phases connect

```
Phase 1: build the reasoning engine
          ↓
Phase 2: add routing in front of it
          ↓
Phase 3: expose it over HTTP with streaming
          ↓
Phase 4: build the Next.js UI that consumes the stream
          ↓
Phase 5: wire everything into Docker and run end-to-end
```

Each phase is testable on its own. Phases 1 and 2 are pure Python — no running containers needed. Phase 3 requires the FastAPI server. Phase 4 requires Phase 3. Phase 5 requires all of them.
