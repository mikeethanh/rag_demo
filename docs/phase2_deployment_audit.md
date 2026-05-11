# Phase 2 — Deployment & Infrastructure Impact Audit

**Scope:** Every change introduced in Phase 2 of the LangGraph + AG-UI migration.
**System baseline:** Same as Phase 1 — Ubuntu 22.04 container, Python 3.10 (3.11 in conda env), FastAPI + Uvicorn, Celery + Valkey, Qdrant, MariaDB. Phase 1 code is present but still inert in production.

---

## Summary table

| Change | Type | Risk | Docker rebuild | CI/CD change | Backward compatible |
|---|---|---|---|---|---|
| `agent/state.py` — added `branch` field | Modified file | Safe | Yes | No | Yes |
| `agent/router.py` — new file | New file | Safe | Yes | No | Yes |
| `agent/branches/general.py` — new file | New file | Safe | Yes | No | Yes |
| `agent/branches/calculation.py` — new file | New file | Safe | Yes | No | Yes |
| `agent/branches/web_search.py` — new file | New file | Safe | Yes | No | Yes |
| `agent/main_graph.py` — new file | New file | Safe | Yes | No | Yes |
| `tests/test_router.py` — new file | New file | Safe | No (test-only) | Recommended | Yes |
| `tests/test_branches.py` — new file | New file | Safe | No (test-only) | Recommended | Yes |
| No `requirements.txt` changes | — | Safe | No | No | Yes |
| No env var changes (active) | — | Safe | No | No | Yes |
| No API endpoint changes | — | Safe | No | No | Yes |
| No Celery/Valkey changes | — | Safe | No | No | Yes |

**Overall Phase 2 production risk: LOW.** All new code is still inert in production — `main_graph.py` is never called from `app.py` or `tasks.py`. No live code paths are touched.

---

## 1. Modified files

### `backend/src/agent/state.py`

**Change:** Added `branch: str` field to `GraphState`.

```python
# Before
class GraphState(TypedDict, total=False):
    query: str
    documents: list
    generation: str
    transformation_count: int
    follow_up_questions: list
    source_documents: list
    _grade_avg: float

# After — one field added
class GraphState(TypedDict, total=False):
    ...
    branch: str   # "legal" | "general" | "calculation" | "web_search"
```

| Impact | Assessment |
|---|---|
| Deployment impact | None — `GraphState` is only instantiated when the graph runs, which is not wired to any live endpoint in Phase 2 |
| Docker rebuild required | Yes — file changed |
| CI/CD change required | No |
| Backward compatible | **Yes** — `total=False` means `branch` is optional. All Phase 1 code and tests that construct `GraphState` without a `branch` key continue to work. `TestRollbackRouter`, `TestRewriteNode`, etc. all pass unchanged (52/52 total) |
| Rollback safety | Safe — revert the line addition |

**Risk: Safe**

---

## 2. New files

### `backend/src/agent/router.py`

**Purpose:** `classify` node + `branch_router` conditional edge.

| Impact | Assessment |
|---|---|
| Deployment impact | None — not imported by `app.py` or `tasks.py` in Phase 2 |
| Docker rebuild required | Yes |
| CI/CD change required | No |
| Reverse proxy change | No |
| SSE/streaming concern | No |
| Celery/Valkey impact | None |
| Memory/CPU impact | Negligible at import. When called (Phase 3+): one additional LLM API call per request (the classification call). This adds ~0.3–1s latency and one GPT-4o-mini token cost per request. |
| Backward compatible | Yes — adds nothing to any existing code path |
| Rollback safety | Safe — delete the file |

**Production note (Phase 3 relevant):** The classifier adds one LLM call at the front of every request. This is a fixed overhead regardless of which branch is selected. If latency is a concern after Phase 3, consider caching repeated identical queries or using a faster/cheaper model for classification.

**Risk: Safe**

---

### `backend/src/agent/branches/general.py`

| Impact | Assessment |
|---|---|
| Deployment impact | None in Phase 2 |
| Docker rebuild required | Yes |
| CI/CD change required | No |
| Memory/CPU impact | One LLM call when invoked. Same cost as the old `bot_rag_answer_message` Celery task without retrieval |
| Backward compatible | Yes |
| Rollback safety | Safe — delete the file |

**Risk: Safe**

---

### `backend/src/agent/branches/calculation.py`

| Impact | Assessment |
|---|---|
| Deployment impact | None in Phase 2 |
| Docker rebuild required | Yes |
| CI/CD change required | No |
| External service dependency | None — stub tools run entirely in Python, no external calls |
| Tool call loop concern | The loop is capped at 4 iterations. A runaway LLM that keeps emitting tool calls will hit the cap and return a safe fallback message. No infinite loop risk. |
| Memory/CPU impact | Multiple LLM calls (2–4 turns for the tool loop) when invoked. Each tool call is cheap (few tokens). |
| Backward compatible | Yes |
| Rollback safety | Safe |

**Production note:** The two stub tools return hardcoded/formulaic values. They are not connected to any real penalty database or legal reference. This is intentional — Phase 2 establishes the tool-calling interface and routing. Real calculation logic is a future product decision, not a Phase 2 concern.

**Risk: Safe**

---

### `backend/src/agent/branches/web_search.py`

| Impact | Assessment |
|---|---|
| Deployment impact | None in Phase 2 — Tavily is mocked in tests and this file is not called from any live endpoint |
| Docker rebuild required | Yes |
| CI/CD change required | No |
| External service dependency | **Tavily API** — requires `TAVILY_API_KEY` env var and `tavily-python` package. Neither is present yet in Phase 2. The import is lazy (inside `_tavily_search()`), so the module imports cleanly even without the package installed. |
| Failure mode | If Tavily is unavailable or returns an error, `_tavily_search()` catches the exception and returns `[]`. The branch then returns a safe `"No web search results found"` message. No crash, no 500 error. |
| Network impact | When active (Phase 3+): each web_search branch invocation makes an outbound HTTPS request to Tavily's API. The Docker container must have outbound internet access. Current `internal-network` setup allows this since there is no egress firewall. |
| Memory/CPU impact | Two LLM calls (rewriter + summarizer) plus one Tavily API call per request |
| Backward compatible | Yes |
| Rollback safety | Safe |

**Actions required before Phase 3:**
1. Add `tavily-python` to `requirements.txt`
2. Add `TAVILY_API_KEY` to the `.env` file and `docker-compose.yml` environment section

**Risk: Safe** (in Phase 2 context)

---

### `backend/src/agent/main_graph.py`

**Purpose:** Top-level graph wiring router + all four branches.

| Impact | Assessment |
|---|---|
| Deployment impact | None in Phase 2 — `get_main_graph()` is never called from `app.py` |
| Docker rebuild required | Yes |
| CI/CD change required | No |
| Memory/CPU impact | When `get_main_graph()` is first called (Phase 3+): graph compilation is a one-time cost of ~15–25 MB RAM per Uvicorn worker process, slightly higher than Phase 1 (`build_legal_graph`) because more nodes are registered. With `--workers 2`, total additional RAM: ~50–80 MB. |
| Singleton concern | `get_main_graph()` caches the compiled graph in a module-level variable. Each Uvicorn worker process has its own copy of the module — this is correct. The singleton is process-scoped, not process-shared, so there is no cross-process state leak. |
| Backward compatible | Yes |
| Rollback safety | Safe — delete the file and `phase 1`'s `legal_graph.py` singleton is unaffected |

**Design note:** `main_graph.py` re-registers all legal branch nodes directly instead of importing and nesting `legal_graph.py` as a sub-graph. This keeps the graph topology flat, which produces clearer AG-UI `STATE_DELTA` events in Phase 3 (one event per top-level node, not nested sub-graph events).

**Risk: Safe**

---

### `backend/tests/test_router.py`

| Impact | Assessment |
|---|---|
| Deployment impact | None |
| Docker rebuild required | No (test-only) |
| CI/CD change required | **Recommended** — add `pytest tests/test_router.py` as a CI step |
| Backward compatible | Yes |
| Rollback safety | Safe |

**Risk: Safe**

---

### `backend/tests/test_branches.py`

| Impact | Assessment |
|---|---|
| Deployment impact | None |
| Docker rebuild required | No (test-only) |
| CI/CD change required | **Recommended** — add `pytest tests/test_branches.py` as a CI step |
| Notable test pattern | `TestCalculationBranch` uses `MagicMock()` objects as return values (not strings) because `calculation_answer` calls `openai_chat_complete(raw=True)` and inspects `.tool_calls` on the result. All other branch tests return plain strings. |
| Backward compatible | Yes |
| Rollback safety | Safe |

**Risk: Safe**

---

## 3. No-change items (explicitly confirmed)

| Item | Status | Notes |
|---|---|---|
| `app.py` | Unchanged | `/chat/complete` endpoint still active and functional |
| `tasks.py` | Unchanged | All Celery tasks still registered and operational |
| `database.py` | Unchanged | No change |
| `cache.py` | Unchanged | Valkey session TTL logic — no change |
| `summarizer.py` | Unchanged | `langchain-core` conflict risk from Phase 1 still present |
| `agent/nodes.py` | Unchanged | Phase 1 nodes unchanged |
| `agent/legal_graph.py` | Unchanged | Phase 1 graph unchanged |
| `agent/checkpointer.py` | Unchanged | Phase 1 checkpointer unchanged |
| `requirements.txt` | Unchanged | No new packages in Phase 2 |
| `docker-compose.yml` | Unchanged | All services unchanged |
| `Dockerfile` | Unchanged | No change |
| API endpoints | Unchanged | `POST /chat/complete`, `GET /chat/complete/{task_id}`, `POST /document/create`, `POST /collection/create` — all unchanged |
| Celery/Valkey | Unchanged | Broker and result backend fully operational |
| Qdrant | Unchanged | Collection `"llm"` and vector config unchanged |
| MariaDB | Unchanged | Schema unchanged |
| Streamlit UI | Unchanged | Still polling `GET /chat/complete/{task_id}` |

---

## 4. Environment variables

### Currently active (no change)

All existing env vars remain unchanged. No new env vars are active in Phase 2.

### New env var introduced (not yet active)

```
TAVILY_API_KEY=<your_tavily_key>
```

Read by `web_search.py` at module level (`os.environ.get("TAVILY_API_KEY", "")`). If the key is absent, the string is empty — Tavily will return an authentication error, which is caught by `_tavily_search()` and handled gracefully (returns empty list → safe fallback message). The key has no effect in Phase 2 since the web search branch is never called from a live endpoint.

**Action required before Phase 3:**
1. Obtain a Tavily API key from [tavily.com](https://tavily.com)
2. Add `TAVILY_API_KEY=...` to `backend/.env`
3. Add to `docker-compose.yml` under `chatbot-api` environment section
4. Add `tavily-python` to `requirements.txt`

---

## 5. API changes

**None in Phase 2.** All endpoints are byte-for-byte identical to Phase 1 and the original system.

---

## 6. Async / streaming changes

**None in Phase 2.** All new branch functions are synchronous, consistent with Phase 1 nodes. No `async def`, no `asyncio`, no SSE. Uvicorn configuration is unchanged.

The same Phase 1 notes about SSE changes apply — all streaming work is scoped to Phase 3.

---

## 7. Memory and CPU impact

### Phase 2 (current) — no change from Phase 1
New code files are loaded into the Python interpreter but none of the new `agent/` modules are imported by `app.py` or `tasks.py`. Import cost is zero.

### Phase 3 onwards (forecast)

When `get_main_graph()` is first called per Uvicorn worker:

| Item | Estimate |
|---|---|
| Graph compilation RAM | ~20–30 MB per worker (more nodes than Phase 1's legal-only graph) |
| SQLite checkpointer connection | ~2–5 MB per worker |
| Total additional RAM (2 workers) | ~50–70 MB |

**Per-request cost after Phase 3 (new costs vs old pipeline):**

| Branch | LLM calls | External API calls | Notes |
|---|---|---|---|
| Router (always) | +1 | None | Classification call, ~200–500 tokens |
| Legal | +6–8 | Qdrant search | Rewrite + retrieve + grade×6 + generate + follow_up |
| General | +1 | None | Single generation call |
| Calculation | +2–4 | None | Tool loop: 1 classifier + 1–2 tool turns + 1 final |
| Web Search | +2 | Tavily (1 call) | Rewriter + summarizer |

The legal branch is the most expensive: up to 9 LLM calls per request (1 router + 1 rewrite + 6 grade + 1 generate + 1 follow-up) on a fresh query that grades well on the first try. With rollback, add 1 rewrite + 6 grade calls per retry.

---

## 8. Celery / Valkey behavior

**No change in Phase 2.** Celery worker and Valkey are completely unaffected. The new `agent/` code has no awareness of Celery and vice versa.

Celery and Valkey are removed in Phase 3.

---

## 9. Backward compatibility

All existing behavior is preserved:
- `/chat/complete` POST and GET polling — identical
- `/document/create` — identical
- Streamlit UI — identical
- Celery task queue — identical
- MariaDB reads/writes — identical
- Phase 1 tests — all 18 still pass (52/52 total with Phase 2 tests)

**Phase 2 introduces zero breaking changes.**

---

## 10. Deployment commands

### If deploying Phase 2 to an existing running environment:

```bash
# 1. Pull the new code
git pull origin main

# 2. Rebuild the API container (new source files copied in)
cd backend
docker compose build chatbot-api

# 3. chatbot-worker does not need a rebuild in Phase 2
#    (no requirements.txt changes, no changes to files it imports)

# 4. Restart API container
docker compose up -d chatbot-api

# 5. Verify containers are healthy
docker ps | grep chatbot-api
docker logs --tail=20 chatbot-api
```

### Verify no import errors after rebuild:

```bash
docker exec chatbot-api python -c "import app; print('app OK')"
docker exec chatbot-api python -c "from agent.main_graph import build_main_graph; print('main_graph OK')"
docker exec chatbot-api python -c "from agent.router import classify; print('router OK')"
docker exec chatbot-api python -c "from agent.branches.general import general_answer; print('general OK')"
docker exec chatbot-api python -c "from agent.branches.calculation import calculation_answer; print('calculation OK')"
docker exec chatbot-api python -c "from agent.branches.web_search import web_search_answer; print('web_search OK')"
```

---

## 11. Migration risks

| Risk | Probability | Severity | Mitigation |
|---|---|---|---|
| `langchain-core` version conflict breaks `summarizer.py` (carried from Phase 1) | Medium | High | Still unresolved. Must address before Phase 3 container rebuild. Pin `langchain-core<0.3.0` or accept that `summarizer.py` is removed in Phase 3. |
| `tavily-python` not installed when Phase 3 goes live | High (if forgotten) | Medium | Add to `requirements.txt` before Phase 3. Import is lazy — will fail silently at request time, not at startup. |
| `TAVILY_API_KEY` missing in production env | High (if forgotten) | Medium | Tavily call will fail → caught by exception handler → returns empty list → safe fallback message. Not a crash, but web_search branch will always return "No results found." |
| Router LLM returning unexpected output | Low | Low | Unknown outputs default to `"legal"` branch — the most capable branch. No crash. |
| Calculation tool loop not terminating | Very low | Low | Loop is hard-capped at 4 iterations. Returns fallback message on exhaustion. |
| SQLite checkpointer DB lost on restart (Phase 3+) | High (if not addressed) | Medium | Mount persistent volume in Phase 5. |

---

## 12. Rollback steps

Phase 2 is fully reversible with zero production impact (since it is not yet active):

```bash
# Remove Phase 2 additions only (keep Phase 1 intact)
rm backend/src/agent/router.py
rm backend/src/agent/main_graph.py
rm -rf backend/src/agent/branches/
rm backend/tests/test_router.py
rm backend/tests/test_branches.py

# Revert state.py (remove branch field)
git checkout backend/src/agent/state.py

# Rebuild API container
cd backend
docker compose build chatbot-api
docker compose up -d chatbot-api
```

Phase 1 code (`legal_graph.py`, `nodes.py`, etc.) is preserved by this rollback. The system returns to the Phase 1 state — agent code present but inert.

---

## 13. Smoke tests to validate deployment

```bash
# 1. Existing endpoint still works (unchanged)
curl -s -X POST http://localhost:8000/chat/complete \
  -H "Content-Type: application/json" \
  -d '{"bot_id":"botFinance","user_id":"smoketest","message":"Hello"}' \
  | jq '.task_id'
# Expected: a non-null task_id string

# 2. All new agent modules import cleanly
docker exec chatbot-api python -c "
from agent.state import GraphState
from agent.router import classify, branch_router
from agent.branches.general import general_answer
from agent.branches.calculation import calculation_answer
from agent.branches.web_search import web_search_answer
from agent.main_graph import build_main_graph
print('All Phase 2 imports OK')
"

# 3. Run Phase 2 tests inside container
docker exec chatbot-api python -m pytest \
  /usr/src/app/tests/test_router.py \
  /usr/src/app/tests/test_branches.py \
  -v --tb=short
# Expected: 34 passed

# 4. Run full test suite (Phase 1 + Phase 2) — no regressions
docker exec chatbot-api python -m pytest /usr/src/app/tests/ -v --tb=short
# Expected: 52 passed
```

---

## 14. Immediate action items (before Phase 3)

| Priority | Action | Why |
|---|---|---|
| **High** | Add `langchain-core>=0.2.0,<0.3.0` pin to `requirements.txt` | Carried from Phase 1 — prevent conflict with `summarizer.py` when rebuilding container |
| **High** | Add `tavily-python` to `requirements.txt` | Required for live web_search branch in Phase 3 |
| **High** | Add `TAVILY_API_KEY` to `backend/.env` and `docker-compose.yml` | Required for Tavily calls in Phase 3 |
| **Medium** | Move `pytest` and `pytest-asyncio` to `requirements-dev.txt` | Carried from Phase 1 — test packages should not be in the production image |
| **Medium** | Add `pytest tests/test_router.py tests/test_branches.py` to CI pipeline | Prevent regressions |
| **Low** | Document `TAVILY_API_KEY` in `.env.example` | Avoid Phase 3/5 surprises |
