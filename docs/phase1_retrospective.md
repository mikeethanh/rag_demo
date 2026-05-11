# Phase 1 Retrospective — LangGraph Legal RAG Core

---

## What Phase 1 accomplished

Phase 1 replaced the existing Celery-based linear pipeline with a proper LangGraph stateful graph for the Legal query branch. No existing production code was removed yet — Phase 1 only **adds** the new agent layer alongside the old code. The old `tasks.py` / `app.py` are untouched until Phase 3.

---

## Files created

### `backend/src/agent/__init__.py`
Empty package marker. Makes `agent/` importable as a Python package.

### `backend/src/agent/branches/__init__.py`
Empty package marker. Pre-creates the `branches/` sub-package for Phase 2 (General, Calculation, Web Search branches).

### `backend/src/agent/state.py`
**Purpose:** Defines the single shared state object that flows through every node in the LangGraph graph.

```python
class GraphState(TypedDict, total=False):
    query: str                  # current query (gets rewritten each loop)
    documents: list             # raw Qdrant payloads {title, content}
    generation: str             # final LLM answer
    transformation_count: int   # how many times rewrite has run
    follow_up_questions: list   # 3 suggested follow-up questions
    source_documents: list      # {title, source, page} for UI citations
    _grade_avg: float           # internal: avg relevance score from grade_docs
```

**Key design decision:** `total=False` is set on the TypedDict. This is required because LangGraph merges state updates by key — if a key is not declared in the TypedDict, LangGraph silently drops it. `_grade_avg` is an internal routing key that must survive between `grade_docs` and the conditional edge `rollback_router`, so it must be declared in the schema even though it is not part of the public output.

---

### `backend/src/agent/nodes.py`
**Purpose:** All node functions and the conditional edge router. Each function takes a `GraphState` and returns a partial `GraphState` (only the keys it updates).

| Function | Role | LLM call |
|---|---|---|
| `rewrite` | Rewrites the user query using Chain-of-Thought prompting to improve semantic search recall. Increments `transformation_count`. | Yes — system: `"expert legal query optimizer"` |
| `retrieve` | Embeds the rewritten query via `get_embedding`, searches Qdrant top-6, populates `documents` and `source_documents`. | No |
| `grade_docs` | Calls `_score_document` for each retrieved document. Each call asks the LLM to score relevance 0–1. Averages scores into `_grade_avg`. | Yes per doc — system: `"legal document relevance grader"` |
| `generate` | Builds a full prompt (system + numbered doc context + query) and calls LLM to produce a cited answer. | Yes — system: `"precise legal assistant"` |
| `fallback` | Called when `transformation_count >= 3` and docs are still poor quality. Returns an LLM answer with no citations and clears `source_documents`. | Yes — system: `"legal assistant... not sourced"` |
| `follow_up` | Given the query + documents + generation, asks LLM to produce 3 follow-up questions. Parses the numbered list output. | Yes — system: `"legal research assistant"` |
| `rollback_router` | **Conditional edge function** — not a node. Reads `_grade_avg` and `transformation_count` to decide the next node. No LLM call. | No |

**Routing logic in `rollback_router`:**
```
_grade_avg >= 0.7                              → "generate"
_grade_avg < 0.7 AND transformation_count < 3  → "rewrite"
_grade_avg < 0.7 AND transformation_count >= 3 → "fallback"
```

**Helper `_parse_follow_up(text)`:** Strips leading `1.`, `2.`, `3.`, `-`, `*` from LLM output lines to extract clean question strings. Returns at most 3 items.

---

### `backend/src/agent/legal_graph.py`
**Purpose:** Assembles the LangGraph `StateGraph`, wires all nodes and edges, and compiles it with a checkpointer.

**Graph structure:**
```
START → rewrite → retrieve → grade_docs
                                ├── [avg >= 0.7]          → generate → follow_up → END
                                ├── [avg < 0.7, count < 3] → rewrite  (loop)
                                └── [avg < 0.7, count >= 3] → fallback → END
```

**`build_legal_graph(checkpointer=None)`:** Factory function. Accepts an optional checkpointer so tests can inject `MemorySaver` instead of the SQLite checkpointer. If `None` is passed, it calls `get_checkpointer()` which uses the real SQLite file.

**`get_legal_graph()`:** Singleton accessor that lazily builds the graph once and caches it in a module-level variable. Used by the server layer in later phases.

---

### `backend/src/agent/checkpointer.py`
**Purpose:** Factory for the LangGraph SQLite checkpointer. Reads the DB path from `CHECKPOINTER_DB_PATH` env var (defaults to `/tmp/langgraph_checkpoints.db`).

The checkpointer is what gives the agent memory — it persists `GraphState` after every node so the same `thread_id` resumes conversation context across requests.

```python
_DB_PATH = os.environ.get("CHECKPOINTER_DB_PATH", "/tmp/langgraph_checkpoints.db")

def get_checkpointer():
    return SqliteSaver.from_conn_string(_DB_PATH)
```

**Package installed:** `langgraph-checkpoint-sqlite` (separate from `langgraph` core, must be installed explicitly).

---

### `backend/tests/conftest.py`
**Purpose:** Adds `backend/src/` to `sys.path` before any test module is imported. This lets tests do `from agent.nodes import ...` without needing a full package install. All test files rely on this — it must exist for the test suite to collect.

---

### `backend/tests/test_legal_graph.py`
**Purpose:** Phase 1 test gate. 18 tests across 6 test classes. All LLM and Qdrant calls are mocked — no real API keys or running services required.

| Class | Tests | What is verified |
|---|---|---|
| `TestRollbackRouter` | 5 | Pure routing logic — all 3 branches of `rollback_router` at boundary conditions (0.7 exact, above, below, count at 3, count above 3) |
| `TestRewriteNode` | 1 | `transformation_count` increments correctly; `query` is replaced with LLM output |
| `TestRetrieveNode` | 1 | `documents` and `source_documents` are both populated correctly from Qdrant payloads |
| `TestGradeDocsNode` | 3 | Average score computed correctly; zero docs → 0.0; malformed LLM output → 0.0 (graceful degradation) |
| `TestGenerateNode` | 1 | `generation` field is set from LLM output |
| `TestFallbackNode` | 1 | `generation` set; `source_documents` cleared to `[]`; `follow_up_questions` cleared to `[]` |
| `TestFollowUpNode` | 1 | Numbered list parsed correctly into 3 clean strings |
| `TestLegalGraphEndToEnd` | 5 | Full graph invocations with mocked LLM |

**End-to-end test scenarios:**
- **Happy path** — LLM grades high (0.85) → graph reaches `follow_up` → returns 3 questions + cited answer + sources
- **Rollback then pass** — First grade low (0.3) → rewrite → second grade high (0.9) → generate; asserts `transformation_count >= 2`
- **Fallback after max retries** — LLM always grades 0.1 → rewrite 3 times → fallback node fires → `source_documents == []`
- **Checkpointer persistence** — After one invocation, `graph.get_state(thread_id)` returns the correct `generation` and 3 follow-up questions
- **Final state schema** — Asserts all 3 required output fields (`generation`, `source_documents`, `follow_up_questions`) exist and are the correct Python types

---

## Bugs encountered and fixed

### Bug 1 — `brain.py` eager OpenAI client instantiation

**File:** `backend/src/brain.py`

**Problem:** The original code instantiated the OpenAI client at module load time:
```python
client = get_openai_client()   # line 14 — runs on import
```
`OpenAI()` raises `OpenAIError: Missing credentials` if `OPENAI_API_KEY` is not set in the environment. This caused every test to fail at collection time (before any test ran) with an import error — no API key is set in the local dev/test environment.

**Fix:** Converted to lazy initialization with a module-level `_client = None` sentinel:
```python
_client = None

def get_openai_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client
```
Every function that needs the client now calls `get_openai_client()` at call time instead of at import time. Since all LLM calls are mocked in tests, `get_openai_client()` is never actually called during the test run.

Also removed the unused `from redis import InvalidResponse` import that was in `brain.py` — it was pulling in `redis` as a hard import dependency even though it was never used in that file.

---

### Bug 2 — `_grade_avg` silently dropped by LangGraph state merging

**File:** `backend/src/agent/state.py`

**Problem:** LangGraph merges node return values into the shared state dict by key. Keys that are **not declared** in the `TypedDict` schema are silently dropped during the merge. The original `GraphState` did not include `_grade_avg`. As a result, `grade_docs` would write `_grade_avg` into its return dict, but LangGraph would discard it before passing state to the conditional edge function `rollback_router`. `rollback_router` would then read `state.get("_grade_avg", 0.0)` and always get `0.0`, routing every query to fallback or rewrite — never to `generate`.

**Symptom in debug:** The graph looped through `rewrite → retrieve → grade_docs` exactly 3 times then always hit `fallback`, even when the mock LLM returned `"0.9"` for every grade call.

**Fix:** Added `_grade_avg: float` to `GraphState` and changed the TypedDict declaration to `total=False` (making all keys optional) so LangGraph does not reject partial state updates:

```python
class GraphState(TypedDict, total=False):
    ...
    _grade_avg: float
```

---

### Bug 3 — `langgraph.checkpoint.sqlite` not installed by default

**Problem:** `langgraph` core does not bundle the SQLite checkpointer. Importing `from langgraph.checkpoint.sqlite import SqliteSaver` raised `ModuleNotFoundError`.

**Fix:** Installed the separate package `langgraph-checkpoint-sqlite` and added it to `requirements.txt`:
```
langgraph-checkpoint-sqlite>=2.0.0
```

---

### Bug 4 — Test mock keyword mismatch for `follow_up` node

**Problem:** Initial test mocks checked `if "follow-up" in system` to detect calls to the `follow_up` node. The actual system prompt for that node starts with `"You are a legal research assistant."` — it does not contain the string `"follow-up"`. Mock returned `"ok"` for all follow-up calls, producing empty `follow_up_questions` in end-to-end tests.

**Fix:** Updated all test mocks to check `if "research assistant" in system` to correctly identify the follow-up node's system prompt. Each mock now uses a unique substring from each node's system prompt:

| Node | System prompt substring used in mock |
|---|---|
| `rewrite` | `"optimizer"` |
| `grade_docs` | `"grader"` |
| `generate` | `"precise"` |
| `follow_up` | `"research assistant"` |
| `fallback` | `"not sourced"` or `"general"` |

---

## Dependencies added to `requirements.txt`

```
langgraph>=0.2.0
langgraph-checkpoint-sqlite>=2.0.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

---

## Test gate result

```
pytest tests/test_legal_graph.py -v
======================== 18 passed, 2 warnings in 1.57s ========================
```

All 18 tests pass. The 2 warnings are deprecation notices from `langgraph` and `qdrant_client` libraries — not from our code.
