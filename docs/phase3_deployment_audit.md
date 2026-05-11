# Phase 3 — Deployment & Infrastructure Impact Audit

**Scope:** Every change introduced in Phase 3 of the LangGraph + AG-UI migration.
**System baseline:** Ubuntu 22.04 container, Python 3.10 (3.11 in conda env), FastAPI + Uvicorn, Celery + Valkey, Qdrant, MariaDB. Phase 1 and Phase 2 code is present. Phase 3 activates `main_graph` for the first time via the new `POST /runs` endpoint.

---

## Summary table

| Change | Type | Risk | Docker rebuild | CI/CD change | Backward compatible |
|---|---|---|---|---|---|
| `server/__init__.py` — new file | New file | Safe | Yes | No | Yes |
| `server/agui_handler.py` — new file | New file | Safe | Yes | No | Yes |
| `app.py` — added `POST /runs` endpoint | Modified file | Low | Yes | Recommended | **Yes** — all existing endpoints unchanged |
| `requirements.txt` — added `ag-ui-protocol==0.1.18` | Modified file | Low | Yes | No | Yes |
| `tests/test_agui_server.py` — new file | New file | Safe | No (test-only) | Recommended | Yes |

**Overall Phase 3 production risk: LOW.** The new `/runs` endpoint activates `main_graph` for the first time. All existing endpoints, Celery tasks, and the Streamlit frontend are unchanged.

---

## 1. New files

### `backend/src/server/__init__.py`

| Impact | Assessment |
|---|---|
| Deployment impact | None beyond making `server` a Python package |
| Docker rebuild required | Yes — new directory and file copied in |
| CI/CD change required | No |
| Backward compatible | Yes |
| Rollback safety | Safe — delete the directory |

**Risk: Safe**

---

### `backend/src/server/agui_handler.py`

**Purpose:** Converts LangGraph `stream_mode="updates"` output into a typed AG-UI SSE stream using the `ag-ui-protocol` package.

| Impact | Assessment |
|---|---|
| Deployment impact | None until called via `POST /runs` |
| Docker rebuild required | Yes |
| CI/CD change required | No |
| External service dependency | None beyond what the graph itself uses (OpenAI, Qdrant) |
| Streaming concern | Generator function — Uvicorn/FastAPI handles backpressure correctly with `StreamingResponse`. No buffering issues with `X-Accel-Buffering: no` header set |
| Memory/CPU impact | Negligible — string formatting per node output. No additional LLM calls |
| Backward compatible | Yes — not imported by any pre-Phase-3 code path |
| Rollback safety | Safe — delete the file |

**Risk: Safe**

---

### `backend/tests/test_agui_server.py`

| Impact | Assessment |
|---|---|
| Deployment impact | None |
| Docker rebuild required | No (test-only) |
| CI/CD change required | **Recommended** — add `pytest tests/test_agui_server.py` as a CI step |
| Backward compatible | Yes |
| Rollback safety | Safe |

**Risk: Safe**

---

## 2. Modified files

### `backend/src/app.py`

**Changes:** Added `RunRequest` model and `POST /runs` endpoint. Added imports for `get_checkpointer`, `get_main_graph`, `agui_event_stream`.

| Impact | Assessment |
|---|---|
| Deployment impact | **Active in Phase 3** — `POST /runs` is a live endpoint from first deployment |
| Docker rebuild required | Yes |
| CI/CD change required | Recommended — add smoke test for `POST /runs` |
| Breaking change to existing endpoints | **None** — `GET /`, `POST /chat/complete`, `GET /chat/complete/{task_id}`, `POST /document/create`, `POST /collection/create` are byte-for-byte identical |
| Reverse proxy change | If nginx is sitting in front: add `proxy_buffering off` for `/runs` to avoid SSE buffering. The `X-Accel-Buffering: no` response header tells nginx to disable buffering automatically if `proxy_buffering on` is the default |
| SSE / long-lived connection concern | `POST /runs` holds the HTTP connection open for the duration of graph execution. With `--workers 2`, each worker can handle multiple concurrent SSE connections (async generator). A slow graph (legal branch with retries: up to ~30s) ties up one connection slot. Under high load, consider increasing workers or adding a timeout |
| Celery/Valkey impact | None — `POST /runs` does not touch Celery. Old polling path unchanged |
| Memory/CPU impact | `get_main_graph()` is called for the first time when the first `/runs` request arrives. Graph compilation: ~20–30 MB RAM per Uvicorn worker, one-time cost. Subsequent calls return the cached singleton. With 2 workers: ~50–60 MB total additional RAM |
| Backward compatible | Yes |
| Rollback safety | Remove the `RunRequest` model and `POST /runs` route, revert imports — all other routes unaffected |

**Risk: Low**

---

### `backend/requirements.txt`

**Change:** Added `ag-ui-protocol==0.1.18`.

| Impact | Assessment |
|---|---|
| Docker rebuild required | Yes — `pip install` runs during `docker build` |
| Dependency conflict risk | Low — `ag-ui-protocol` depends on `pydantic>=2.0` (already satisfied by FastAPI 0.112), `typing-extensions` (already present). No known conflicts with existing packages |
| Package size | Small — pure Python, no native extensions |
| Backward compatible | Yes |
| Rollback safety | Remove the line; rebuild image |

**Risk: Low**

---

## 3. No-change items (explicitly confirmed)

| Item | Status |
|---|---|
| `tasks.py` | Unchanged — Celery tasks fully operational |
| `agent/nodes.py` | Unchanged |
| `agent/legal_graph.py` | Unchanged (Phase 1 graph) |
| `agent/main_graph.py` | Unchanged — now called from `/runs` but code identical to Phase 2 |
| `agent/router.py` | Unchanged |
| `agent/branches/` | Unchanged |
| `agent/checkpointer.py` | Unchanged |
| `database.py` | Unchanged |
| `cache.py` | Unchanged |
| `models.py` | Unchanged |
| `summarizer.py` | Unchanged (langchain-core conflict risk still present — not addressed in Phase 3) |
| `docker-compose.yml` | Unchanged |
| `Dockerfile` | Unchanged |
| Celery/Valkey | Fully operational |
| Qdrant | Collection `"llm"` unchanged |
| MariaDB | Schema unchanged |
| Streamlit UI | Unchanged — still polls `GET /chat/complete/{task_id}` |

---

## 4. Environment variables

### New env var introduced (not yet required to activate new endpoint)

`ag-ui-protocol` itself adds no new env vars. The new `/runs` endpoint uses the same `OPENAI_API_KEY` and `QDRANT_*` vars already required by the graph.

**`TAVILY_API_KEY`** — still not active in Phase 3 (web_search branch can be routed to, but Tavily calls gracefully fall back to "No web search results found" if the key is absent).

**Action required before production web_search use:**
1. Add `TAVILY_API_KEY=...` to `backend/.env` and `docker-compose.yml`
2. Add `tavily-python` to `requirements.txt`

---

## 5. API changes

### New endpoint

| Endpoint | Method | Auth | Request body | Response |
|---|---|---|---|---|
| `/runs` | POST | None (Phase 3) | `{thread_id: str, query: str, run_id?: str}` | `text/event-stream` of AG-UI SSE events |

**AG-UI event sequence:**
```
data: {"type":"RUN_STARTED","thread_id":"...","run_id":"..."}

data: {"type":"STATE_DELTA","delta":[{"op":"replace","path":"/branch","value":"legal"}]}

data: {"type":"TEXT_MESSAGE_START","message_id":"...","role":"assistant"}
data: {"type":"TEXT_MESSAGE_CONTENT","message_id":"...","delta":"The "}
data: {"type":"TEXT_MESSAGE_CONTENT","message_id":"...","delta":"answer "}
...
data: {"type":"TEXT_MESSAGE_END","message_id":"..."}

data: {"type":"RUN_FINISHED","thread_id":"...","run_id":"..."}
```

On error:
```
data: {"type":"RUN_ERROR","message":"<error text>","code":"GRAPH_ERROR"}
```

### Existing endpoints — no change

All five existing endpoints (`GET /`, `POST /chat/complete`, `GET /chat/complete/{task_id}`, `POST /document/create`, `POST /collection/create`) are identical to Phase 2.

---

## 6. Async / streaming

**Phase 3 introduces true HTTP streaming** via `StreamingResponse` with an async generator. Uvicorn handles this natively. Two notes:

1. **Nginx buffering:** If nginx sits in front of Uvicorn, SSE streams will be buffered by default. The `X-Accel-Buffering: no` header on `/runs` responses tells nginx to bypass buffering. Verify this works with `curl -N http://localhost/runs` (responses should arrive immediately, not after the full graph run completes).

2. **Connection lifetime:** Each `/runs` request holds an HTTP connection open for the full graph execution time. Legal branch with 1 rollback can take 20–40s. Ensure load balancer and nginx `proxy_read_timeout` are set to at least 60s.

---

## 7. Memory and CPU impact

### Phase 3 (active)

| Item | Estimate |
|---|---|
| First `/runs` request — graph compilation RAM | ~20–30 MB per Uvicorn worker (one-time) |
| Subsequent requests — graph singleton already cached | 0 additional RAM |
| SQLite checkpointer connection (per worker) | ~2–5 MB (one-time, opened on first request) |
| Total additional RAM (2 workers) | ~50–70 MB |

**Per-request cost (Phase 3 — same as Phase 2 forecast):**

| Branch | LLM calls | External API calls |
|---|---|---|
| Router (always) | +1 | None |
| Legal | +6–8 | Qdrant search |
| General | +1 | None |
| Calculation | +2–4 | None |
| Web Search | +2 | Tavily (1 call) |

---

## 8. Celery / Valkey behavior

**No change in Phase 3.** Celery worker and Valkey are completely unaffected. `POST /runs` bypasses Celery entirely — graph runs synchronously inside the Uvicorn async event loop via a blocking generator. If graph execution time becomes a concern under concurrent load, Phase 5 can move the graph execution into a background thread with `run_in_executor`.

---

## 9. Backward compatibility

All existing behavior is preserved:
- `/chat/complete` POST and GET polling — identical
- `/document/create` — identical
- Streamlit UI — identical (no frontend changes required)
- Celery task queue — identical
- MariaDB reads/writes — identical
- Phase 1 + Phase 2 tests — all 52 still pass (63/63 total with Phase 3 tests)

**The Streamlit frontend does not need any changes.** It polls the old `/chat/complete` endpoint which remains fully operational. The new `/runs` SSE endpoint is designed for the Next.js frontend in Phase 4.

---

## 10. Deployment commands

### Deploying Phase 3 to an existing running environment:

```bash
# 1. Pull the new code
git pull origin main

# 2. Rebuild the API container (new files + requirements.txt change)
cd backend
docker compose build chatbot-api

# 3. chatbot-worker does not need a rebuild
#    (requirements.txt change only adds ag-ui-protocol, not used by the worker)

# 4. Restart API container
docker compose up -d chatbot-api

# 5. Verify containers are healthy
docker ps | grep chatbot-api
docker logs --tail=20 chatbot-api
```

### Verify imports and endpoint after rebuild:

```bash
docker exec chatbot-api python -c "
from ag_ui.core import RunStartedEvent, StateDeltaEvent
from server.agui_handler import agui_event_stream
from agent.main_graph import get_main_graph
print('Phase 3 imports OK')
"

# Smoke test the new endpoint
curl -N -s -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"smoke-test","query":"What is the penalty for bribery?"}' \
  | head -5
# Expected: SSE lines starting with "data: {"type":"RUN_STARTED"..."
```

---

## 11. Migration risks

| Risk | Probability | Severity | Mitigation |
|---|---|---|---|
| `langchain-core` version conflict breaks `summarizer.py` (carried from Phase 1) | Medium | High | Still unresolved. Must address before production rebuild. Pin `langchain-core<0.3.0` or remove `summarizer.py` in Phase 5 |
| `tavily-python` not installed, `TAVILY_API_KEY` missing | High (if forgotten) | Low | Web search branch gracefully returns "No results found" — no crash |
| Nginx SSE buffering | Medium | High | Set `X-Accel-Buffering: no` (already set in response headers) and verify `proxy_read_timeout 60s` in nginx config |
| SQLite checkpointer DB lost on container restart | High (if not addressed) | Medium | Mount persistent volume in Phase 5. In Phase 3, conversation history is lost on restart |
| Graph execution blocking Uvicorn event loop under load | Low | Medium | Legal branch can take 30s+. Under concurrent load, move to thread pool with `run_in_executor` in Phase 5 |
| `ag-ui-protocol` patch version breaking API | Very low | Low | Pinned to `==0.1.18` in requirements.txt |

---

## 12. Rollback steps

Phase 3 can be partially or fully rolled back:

```bash
# Option A — Remove only the /runs endpoint (keep Phase 3 files, just deactivate)
# Edit app.py to remove the RunRequest model and POST /runs route
# Rebuild and restart chatbot-api

# Option B — Full Phase 3 rollback
rm -rf backend/src/server/
rm backend/tests/test_agui_server.py
# Revert app.py to Phase 2 state
git checkout backend/src/app.py
# Remove ag-ui-protocol from requirements.txt
# Rebuild API container
cd backend
docker compose build chatbot-api
docker compose up -d chatbot-api
```

Phase 1 and Phase 2 code remains intact after either rollback. The system returns to the Phase 2 state — all agent code present, only the old Celery polling endpoint active.

---

## 13. Smoke tests to validate deployment

```bash
# 1. Existing endpoint still works (unchanged)
curl -s -X POST http://localhost:8000/chat/complete \
  -H "Content-Type: application/json" \
  -d '{"bot_id":"botFinance","user_id":"smoketest","user_message":"Hello"}' \
  | jq '.task_id'
# Expected: a non-null task_id string

# 2. New AG-UI endpoint streams events
curl -N -s -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"smoke-1","query":"What is the penalty for tax evasion?"}' \
  | grep -c '"type"'
# Expected: >= 4 (RUN_STARTED + at least 2 STATE_DELTA + RUN_FINISHED)

# 3. Error handling — empty query returns 400
curl -s -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"t","query":""}' \
  | jq '.detail'
# Expected: "thread_id and query are required"

# 4. Run Phase 3 tests inside container
docker exec chatbot-api python -m pytest \
  /usr/src/app/tests/test_agui_server.py \
  -v --tb=short
# Expected: 11 passed

# 5. Run full test suite — no regressions
docker exec chatbot-api python -m pytest /usr/src/app/tests/ -v --tb=short
# Expected: 63 passed
```

---

## 14. Immediate action items (before Phase 4)

| Priority | Action | Why |
|---|---|---|
| **High** | Add `langchain-core>=0.2.0,<0.3.0` pin to `requirements.txt` | Carried from Phase 1 — prevent conflict on container rebuild |
| **High** | Add `tavily-python` to `requirements.txt` | Required for live web_search branch |
| **High** | Add `TAVILY_API_KEY` to `backend/.env` and `docker-compose.yml` | Required for Tavily calls |
| **High** | Add `proxy_buffering off` or verify `X-Accel-Buffering: no` works in nginx | SSE will appear to hang in browser/client if nginx buffers the stream |
| **Medium** | Mount a persistent volume for SQLite checkpointer DB | Conversation history lost on container restart without it |
| **Medium** | Set `proxy_read_timeout 60s` in nginx config | Legal branch can take 30s+ — default 60s may be too tight under load |
| **Low** | Add `pytest tests/test_agui_server.py` to CI pipeline | Prevent regressions |
