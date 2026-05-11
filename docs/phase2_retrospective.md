# Phase 2 Retrospective — Semantic Router + All Branches

---

## What Phase 2 accomplished

Phase 2 added a semantic routing layer in front of the Legal RAG graph built in Phase 1. Every incoming query is now classified into one of four branches before any retrieval or generation runs. The three new branches (General, Calculation, Web Search) are fully implemented and independently tested. All four branches normalize their output to the same schema.

No existing production code was removed in Phase 2 — the new code is still inert until Phase 3 wires `main_graph` to an endpoint.

---

## Files created

### `backend/src/agent/router.py`
**Purpose:** Contains the `classify` node and the `branch_router` conditional edge function.

**`classify(state)`** — LangGraph node. Makes a single LLM call with a classification prompt and maps the response to one of four branch names:

| LLM output | Branch |
|---|---|
| `LEGAL` | `"legal"` |
| `GENERAL` | `"general"` |
| `CALCULATION` | `"calculation"` |
| `AMBIGUOUS` | `"web_search"` |
| anything else | `"legal"` (default) |

The mapping is case-insensitive and whitespace-tolerant. Any unrecognised output defaults to `"legal"` to ensure the safest fallback (legal branch has the most robust retrieval + grading pipeline).

**`branch_router(state)`** — conditional edge function (no LLM call). Reads `state["branch"]` and returns it as the routing key. Defaults to `"legal"` if the key is missing.

**System prompt discriminator:** `"classifier"` — used in test mocks to identify router calls.

---

### `backend/src/agent/branches/general.py`
**Purpose:** Direct LLM answer branch for non-legal, non-calculation queries.

Single node: `general_answer(state)`. Makes one `openai_chat_complete` call with a general-purpose system prompt. Returns `{generation, source_documents: [], follow_up_questions: []}`.

No retrieval, no tools, no follow-up generation. This branch is intentionally minimal.

**System prompt discriminator:** `"helpful assistant"`.

---

### `backend/src/agent/branches/calculation.py`
**Purpose:** Stub penalty calculator branch using OpenAI function calling.

**Tool definitions (two stubs):**

| Tool | Input | Stub behavior |
|---|---|---|
| `penalty_calculator(offense_type, base_amount)` | offense type string + amount in VND | Returns `base_amount × 0.20`, minimum 5,000,000 VND |
| `apply_factors(base_penalty, mitigating_factors)` | penalty + list of factor strings | Applies reductions: `first_offense` → 10%, `voluntary_disclosure` → 15%, `cooperation` → 5%. Max total reduction: 30%. |

**`calculation_answer(state)`** — runs an agentic tool-call loop (max 4 turns). Each iteration:
1. Calls `openai_chat_complete(messages, raw=True)` to get the raw message object (not just the string content)
2. If the response has `tool_calls` — executes each tool via `_run_tool()`, appends tool result messages, loops
3. If the response has no `tool_calls` — LLM produced a final text answer, returns it

If the loop exhausts 4 turns without a text answer, returns a safe fallback message.

**Key design decision:** `openai_chat_complete` is called with `raw=True` in this branch only — it needs the full `ChatCompletionMessage` object to inspect `tool_calls`. All other branches use the default `raw=False` (returns string content only).

---

### `backend/src/agent/branches/web_search.py`
**Purpose:** Tavily web search branch for ambiguous or current-events queries.

Three inline nodes (not registered as LangGraph nodes — this branch is a single LangGraph node `web_search_answer` that runs all three steps internally):

| Step | What it does |
|---|---|
| Query rewriter | LLM rewrites query for web search — removes conversational filler, adds keywords |
| Tavily search | Calls `TavilyClient.search()` with `max_results=5`. Lazy import — `tavily` is only imported when the function runs, not at module load |
| Result summarizer | LLM synthesizes Tavily results into a cited answer using `[1]`, `[2]` etc. |

If Tavily returns zero results (network error, no key, or genuinely no results), the branch returns a safe `"No web search results found"` message rather than raising.

**New env var:** `TAVILY_API_KEY` — read at module level. Not yet required (Tavily is mocked in all Phase 2 tests).

---

### `backend/src/agent/main_graph.py`
**Purpose:** Top-level `StateGraph` that wires the router to all four branches.

**`build_main_graph(checkpointer=None)`** — factory function with injectable checkpointer for tests.

**`get_main_graph()`** — production singleton, lazily built on first call.

**Graph structure:**
```
START → classify
          ├── "legal"       → rewrite → retrieve → grade_docs → [rollback_router] → ...
          ├── "general"     → general_answer → END
          ├── "calculation" → calculation_answer → END
          └── "web_search"  → web_search_answer → END
```

The legal branch is embedded directly in `main_graph` — all the nodes from Phase 1 (`rewrite`, `retrieve`, `grade_docs`, `generate`, `fallback`, `follow_up`) are registered in this graph alongside the new branch nodes. The legal graph from Phase 1 (`legal_graph.py`) is not imported by `main_graph.py` — nodes are re-registered directly, which keeps the graph topology flat and avoids sub-graph nesting.

---

## Files modified

### `backend/src/agent/state.py`
**Change:** Added `branch: str` field to `GraphState`.

```python
class GraphState(TypedDict, total=False):
    ...
    branch: str   # "legal" | "general" | "calculation" | "web_search"
```

**Why:** The `classify` node writes `branch` into state. The `branch_router` conditional edge reads it. LangGraph requires all state keys to be declared in the TypedDict or they are silently dropped (same bug as `_grade_avg` in Phase 1 — learned from that experience and declared `branch` upfront).

**Backward compatible:** Yes — `total=False` means the field is optional. Phase 1 tests pass unchanged because `GraphState` with `total=False` accepts state objects without the `branch` key.

---

### `backend/tests/test_router.py` (new)
**Purpose:** Phase 2 test gate for routing logic. 13 tests across 2 test classes.

| Class | Tests | What is verified |
|---|---|---|
| `TestClassifyNode` | 8 | LLM output → branch mapping. Covers all 4 categories + case-insensitivity + whitespace stripping + unknown output default + query preservation |
| `TestBranchRouter` | 5 | Conditional edge reads `branch` field correctly for all 4 branches + missing branch defaults to `"legal"` |

---

### `backend/tests/test_branches.py` (new)
**Purpose:** Phase 2 test gate for all branches and end-to-end main graph routing. 21 tests across 6 test classes.

| Class | Tests | What is verified |
|---|---|---|
| `TestGeneralBranch` | 3 | Returns `generation` string; clears `source_documents`; clears `follow_up_questions` |
| `TestCalculationBranch` | 6 | Tool call loop — single tool, two tools in sequence; stub math (20% rule, 5M minimum, 10% first_offense reduction); loop exhaustion fallback |
| `TestWebSearchBranch` | 4 | Returns generation + source URLs; source schema has `title/source/page`; empty results fallback; clears `follow_up_questions` |
| `TestAggregatorSchema` | 3 | All three non-legal branches produce normalized `{generation, source_documents, follow_up_questions}` output |
| `TestMainGraphEndToEnd` | 5 | Full graph invocation for each branch type; legal route runs full Phase 1 graph; `branch` field present in final state |

---

## Test gate result

```
pytest backend/tests/test_router.py backend/tests/test_branches.py -v
======================== 34 passed in 2.22s ========================
```

Full suite (Phase 1 + Phase 2):
```
pytest backend/tests/ -v
======================== 52 passed in 2.27s ========================
```

---

## Bugs encountered and fixed

### Bug 1 — `calculation_answer` receiving string instead of raw message object

**Problem:** `openai_chat_complete()` has a `raw` parameter that defaults to `False`, returning only `response.choices[0].message.content` (a string). The `calculation_answer` function needs the full `ChatCompletionMessage` object to inspect `.tool_calls`. Initially, the call was made without `raw=True`, causing `AttributeError: 'str' object has no attribute 'tool_calls'`.

**Fix:** Changed the call inside `calculation_answer` to `openai_chat_complete(messages, raw=True)`. All other branches continue using `raw=False`.

**Test impact:** Test mocks for the calculation branch must return a `MagicMock` with `.tool_calls` and `.content` attributes, not a plain string. This is why `TestCalculationBranch` uses `MagicMock()` objects as return values while all other branch tests use plain strings.

---

### Bug 2 — Tavily import at module load time

**Problem:** Initial implementation had `from tavily import TavilyClient` at the top of `web_search.py`. This caused `ModuleNotFoundError` at import time since `tavily-python` is not installed in the conda env yet (not in `requirements.txt`).

**Fix:** Moved the import inside the `_tavily_search()` function body so it only runs when the branch is actually invoked. Tests mock `_tavily_search` directly and never trigger the import. This also makes the module safely importable in environments where `tavily-python` is not installed.

---

### Bug 3 — `branch` field missing from `GraphState` drops silently

**Problem:** First draft of `router.py` returned `{**state, "branch": branch}` from `classify()` — but `branch` was not yet declared in `GraphState`. LangGraph silently dropped the key during state merge (same class of bug as `_grade_avg` in Phase 1). `branch_router` then read `state.get("branch", "legal")` and always returned `"legal"` regardless of the LLM output.

**Symptom in debug:** End-to-end tests for non-legal branches always ran the legal sub-graph, even when the router mock returned `"GENERAL"`.

**Fix:** Added `branch: str` to `GraphState` in `state.py`. Applied the Phase 1 lesson upfront — whenever a new key is added to a node's return dict, it must be added to the TypedDict schema immediately.

---

## Dependencies added

No new packages were added to `requirements.txt` in Phase 2. All code is built on packages already installed in Phase 1 (`langgraph`, `openai`).

**`tavily-python` is not yet installed.** It is referenced lazily inside `_tavily_search()` but not required for Phase 2 tests since that function is fully mocked. It will be added to `requirements.txt` before Phase 3 when the live endpoint needs real Tavily calls.

---

## Conda environment note

Phase 2 was developed and tested entirely inside the `rag_demo` conda environment created at the start of Phase 2. To run the test gate:

```bash
conda activate rag_demo
cd /path/to/rag_demo
python -m pytest backend/tests/test_router.py backend/tests/test_branches.py -v
```
