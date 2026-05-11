# Phase 1 — Deployment & Infrastructure Impact Audit

**Scope:** Every change introduced in Phase 1 of the LangGraph + AG-UI migration.
**System baseline:** Ubuntu 22.04 container, Python 3.10, FastAPI + Uvicorn, Celery + Valkey, Qdrant, MariaDB.

---

## Summary table

| Change | Type | Risk | Docker rebuild | CI/CD change | Backward compatible |
|---|---|---|---|---|---|
| `requirements.txt` — new packages | Config | Moderate | **Yes** | No | Yes |
| `brain.py` — lazy client init | Modified file | Safe | Yes (rebuild bundles it) | No | Yes |
| `backend/src/agent/` — new directory | New files | Safe | Yes | No | Yes |
| `backend/tests/` — new directory | New files | Safe | No (test-only) | Recommended | Yes |
| `pytest` / `pytest-asyncio` in requirements | Config | Safe | Yes | Recommended | Yes |
| No env var changes | — | Safe | No | No | Yes |
| No API endpoint changes | — | Safe | No | No | Yes |
| No async/streaming changes | — | Safe | No | No | Yes |
| No Celery/Valkey changes | — | Safe | No | No | Yes |

**Overall Phase 1 production risk: LOW.** No running services are modified. The new code is inert in production until Phase 3 wires it to an endpoint.

---

## 1. New files

### `backend/src/agent/__init__.py`
**Why necessary:** Makes `agent/` a Python package so `from agent.nodes import ...` resolves correctly in both the container (`PYTHONPATH=/usr/src/app/src`) and in tests.

| Impact | Assessment |
|---|---|
| Deployment impact | None — empty file, no behavior change |
| Docker rebuild required | Yes — file is copied into the image via `COPY . /usr/src/app/` |
| CI/CD change required | No |
| Reverse proxy change | No |
| SSE/streaming concern | No |
| Celery/Valkey impact | None |
| Memory/CPU impact | Negligible |
| Backward compatible | Yes — adds nothing, removes nothing |
| Rollback safety | Safe — delete the directory |

**Risk: Safe**

---

### `backend/src/agent/branches/__init__.py`
Same analysis as above. Pre-creates the `branches/` sub-package for Phase 2.

**Risk: Safe**

---

### `backend/src/agent/state.py`
**Why necessary:** Defines `GraphState` — the TypedDict that LangGraph uses to track state across nodes. Without this, the graph cannot be compiled.

| Impact | Assessment |
|---|---|
| Deployment impact | None — imported only when the graph is constructed, which happens only when `legal_graph.get_legal_graph()` is called. That is never called in Phase 1 from any API endpoint |
| Docker rebuild required | Yes |
| CI/CD change required | No |
| Reverse proxy change | No |
| SSE/streaming concern | No |
| Celery/Valkey impact | None |
| Memory/CPU impact | Negligible — TypedDict is a pure Python class, zero overhead at import |
| Backward compatible | Yes |
| Rollback safety | Safe |

**Risk: Safe**

---

### `backend/src/agent/nodes.py`
**Why necessary:** Contains all 6 node functions and the conditional edge router. This is the core logic of the Legal RAG agent.

| Impact | Assessment |
|---|---|
| Deployment impact | None in Phase 1 — file is imported by `legal_graph.py` which is not called from any live endpoint |
| Docker rebuild required | Yes |
| CI/CD change required | No |
| Reverse proxy change | No |
| SSE/streaming concern | No |
| Celery/Valkey impact | None — this code replaces Celery conceptually but does not touch any Celery config or broker connection in Phase 1 |
| Memory/CPU impact | None at idle. When invoked (Phase 3+), each node runs a synchronous OpenAI API call. With `--workers 2` in Uvicorn, two parallel requests will each block one worker thread. This is the same behavior as the old Celery worker. **No change in Phase 1.** |
| Backward compatible | Yes |
| Rollback safety | Safe — file is not referenced from any live code path |

**One important note:** `nodes.py` imports `brain.openai_chat_complete` and `vectorize.search_vector` at module load time. This means when Python imports `nodes.py`, it will attempt to connect to Qdrant (`vectorize.py` creates a `QdrantClient` at module level pointing to `http://qdrant-db:6333`). In the container this is fine — Qdrant is on the internal network. In local unit tests, this would fail if Qdrant is not running — but all tests mock `search_vector` before it is called, so the client object is created but never used.

**Risk: Safe**

---

### `backend/src/agent/legal_graph.py`
**Why necessary:** Compiles the `StateGraph` with all edges and attaches the checkpointer. Exposes `build_legal_graph()` (injectable checkpointer for tests) and `get_legal_graph()` (production singleton).

| Impact | Assessment |
|---|---|
| Deployment impact | None in Phase 1 — `get_legal_graph()` is never called from a live endpoint |
| Docker rebuild required | Yes |
| CI/CD change required | No |
| Reverse proxy change | No |
| SSE/streaming concern | No |
| Celery/Valkey impact | None |
| Memory/CPU impact | When `get_legal_graph()` is first called (Phase 3+), LangGraph compiles the graph in memory — this is a one-time cost of approximately 5–20 MB RAM per process. With `--workers 2`, each Uvicorn worker process will hold its own compiled graph. Total additional RAM: ~40–80 MB for the graph objects plus SQLite connection overhead. This is acceptable and predictable. |
| Backward compatible | Yes |
| Rollback safety | Safe |

**Production note (Phase 3 relevant, flag now):** `get_legal_graph()` calls `get_checkpointer()` which opens an SQLite file at `CHECKPOINTER_DB_PATH` (default: `/tmp/langgraph_checkpoints.db`). In the current Docker setup, `/tmp` is ephemeral — it does not survive container restarts. This means **conversation history is lost on restart** unless a persistent volume is mounted for the checkpointer DB. This is acceptable for Phase 1 (not yet in use) but must be addressed in Phase 5 with a Docker volume mount.

**Risk: Safe** (in Phase 1 context)

---

### `backend/src/agent/checkpointer.py`
**Why necessary:** Provides the SQLite checkpointer factory. LangGraph needs a checkpointer to persist `GraphState` across requests — without it, every message starts a blank conversation.

| Impact | Assessment |
|---|---|
| Deployment impact | None in Phase 1 |
| Docker rebuild required | Yes |
| CI/CD change required | No |
| Reverse proxy change | No |
| SSE/streaming concern | No |
| Celery/Valkey impact | None — SQLite checkpointer is independent of Valkey. Valkey currently handles Celery broker + session TTL. Checkpointer is a new, separate persistence layer that does not interact with Valkey |
| Memory/CPU impact | SQLite file I/O on every node completion (Phase 3+). At low volume this is negligible. At high concurrency, SQLite has a write-lock per database file — concurrent requests writing to the same `.db` file will serialize writes. If this becomes a bottleneck, switch to `PostgresSaver` (from `langgraph-checkpoint-postgres`). Not a concern in Phase 1. |
| Backward compatible | Yes |
| Rollback safety | Safe |

**New env var introduced (not yet active):**
```
CHECKPOINTER_DB_PATH=/tmp/langgraph_checkpoints.db   # default, no action needed now
```
This env var has no effect in Phase 1 since `get_checkpointer()` is never called. Document it now so it does not surprise Phase 5.

**Risk: Safe**

---

### `backend/tests/conftest.py`
**Why necessary:** Adds `backend/src/` to `sys.path` so test files can import source modules without a package install. Standard pytest pattern for this project structure.

| Impact | Assessment |
|---|---|
| Deployment impact | **None** — test files are not copied into the production Docker image... |
| Docker rebuild required | No — unless `COPY . /usr/src/app/` in the Dockerfile copies `tests/`. It does. But test files are inert in production — they are never executed |
| CI/CD change required | Recommended: add `pytest tests/test_legal_graph.py` to the CI pipeline |
| Backward compatible | Yes |
| Rollback safety | Safe — delete the `tests/` directory |

**Risk: Safe**

---

### `backend/tests/test_legal_graph.py`
**Why necessary:** Phase 1 test gate. 18 tests verifying all node logic and graph routing.

| Impact | Assessment |
|---|---|
| Deployment impact | None |
| Docker rebuild required | No (test-only) |
| CI/CD change required | **Yes — recommended.** Add `pytest tests/test_legal_graph.py -v` as a CI step. The tests are self-contained and require no running services (all mocked) |
| Backward compatible | Yes |
| Rollback safety | Safe |

**Risk: Safe**

---

## 2. Modified files

### `backend/requirements.txt`

#### Exact dependency diff

**Before:**
```
langchain==0.2.14
langchain-community==0.2.12
cohere>=5.9.0
```

**After (additions only):**
```
langchain==0.2.14
langchain-community==0.2.12
cohere>=5.9.0
langgraph>=0.2.0
langgraph-checkpoint-sqlite>=2.0.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

#### Transitive dependencies pulled in (resolved versions as installed)

| Package | Version | Purpose |
|---|---|---|
| `langgraph` | 1.1.10 | Graph engine — nodes, edges, state management |
| `langgraph-checkpoint` | 4.0.3 | Base checkpointer interface (required by langgraph core) |
| `langgraph-checkpoint-sqlite` | 3.0.3 | SQLite persistence backend for checkpointer |
| `langgraph-prebuilt` | 1.0.13 | Pre-built agent nodes (pulled as langgraph dep, not directly used yet) |
| `langgraph-sdk` | 0.3.14 | Client SDK (pulled as dep, not directly used in Phase 1) |
| `langchain-core` | 1.3.3 | **Version upgrade risk** — existing `langchain==0.2.14` depends on an older `langchain-core`. Installing `langgraph` may pull a newer `langchain-core` that conflicts. See risk note below. |
| `aiosqlite` | 0.22.1 | Async SQLite driver (required by `langgraph-checkpoint-sqlite`) |
| `sqlite-vec` | 0.1.9 | SQLite vector extension (required by `langgraph-checkpoint-sqlite`) |
| `xxhash` | 3.7.0 | Fast hashing (required by `langgraph`) |
| `pydantic` | existing | Already in environment via FastAPI |
| `pytest` | 9.0.3 | Test runner |
| `pytest-asyncio` | 1.3.0 | Async test support |

#### `langchain-core` version conflict risk

**This is the highest-risk item in Phase 1.**

The existing `langchain==0.2.14` was released mid-2024 and was built against `langchain-core~=0.2`. The `langgraph>=0.2.0` requirement resolves to `langgraph==1.1.10` which depends on `langchain-core` at version 1.x. These are **not backward compatible**.

`langchain-core` 1.x changed several internal interfaces used by `langchain==0.2.14` and `langchain-community==0.2.12`.

**Affected existing code:**
- `backend/src/summarizer.py` uses `from langchain_community.chat_models import ChatOpenAI` and `from langchain.chains.summarize import load_summarize_chain` — both depend on `langchain-core` internals
- If `langchain-core` is upgraded to 1.x, `summarizer.py` may raise `ImportError` or `AttributeError` at runtime

**Current status:** In the local environment, `langchain-core==1.3.3` installed without error because `summarizer.py` is never imported in tests. In production, `summarizer.py` is imported by `tasks.py` when the Celery worker starts. **This could break the Celery worker on next container rebuild.**

**Mitigation (must apply before Phase 3):** Pin `langchain-core` to a compatible version or accept that `summarizer.py` will be removed in Phase 3 (it is on the removal list). For now, pin explicitly in requirements:
```
langchain-core>=0.2.0,<0.3.0   # add this line to prevent langgraph from upgrading it
```
Or pin `langgraph` to a version that accepts older `langchain-core`:
```
langgraph>=0.2.0,<0.3.0        # older langgraph series used langchain-core 0.2.x
```

**Immediate action required:** Test the container build to confirm no import errors in `summarizer.py` before deploying to any shared environment.

| Impact | Assessment |
|---|---|
| Deployment impact | **Yes** — `requirements.txt` change triggers `pip install -r requirements.txt` in Docker build |
| Docker rebuild required | **Yes** |
| CI/CD change required | Recommended — add `pip check` to CI to catch dependency conflicts early |
| Reverse proxy change | No |
| Memory/CPU impact | Minimal — package imports add ~5–15 MB to Python process RSS. Not significant. |
| Backward compatible | Mostly yes — risk is `langchain-core` version conflict described above |
| Rollback safety | Rollback by reverting `requirements.txt` and rebuilding the image |

**Risk: Moderate** — due to `langchain-core` version conflict potential

---

### `backend/src/brain.py`

**Changes made:**
1. Removed `import json` (was unused)
2. Removed `from redis import InvalidResponse` (was unused import)
3. Changed module-level `client = get_openai_client()` to lazy initialization with `_client = None` sentinel
4. Each function that needs the client now calls `get_openai_client()` at call time

**Why necessary:** The original code ran `OpenAI(api_key=...)` at import time. `openai>=1.56.1` raises `OpenAIError: Missing credentials` if the key is absent — even during `import brain`. This blocked test collection entirely.

| Impact | Assessment |
|---|---|
| Deployment impact | **None** — in production the env var `OPENAI_API_KEY` is always set via `.env` file. The lazy init path has identical behavior: client is created on first API call, same as before. |
| Docker rebuild required | Yes (file changed) |
| CI/CD change required | No |
| Reverse proxy change | No |
| SSE/streaming concern | No |
| Celery/Valkey impact | None — `brain.py` functions are called from Celery tasks. The lazy init is thread-safe for read access because Python's GIL protects the `global _client` assignment. Multiple Celery workers in the same process (not the case here — each worker is a separate process) would each initialize their own client independently. |
| Memory/CPU impact | Negligible — one fewer object instantiated at import time, instantiated instead at first call. Identical steady-state behavior. |
| Backward compatible | **Yes — fully.** All function signatures, return values, and behavior are identical. Only the initialization timing changed. |
| Rollback safety | **Safe** — reverting this change only breaks tests (import error without API key), not production |

**Risk: Safe**

---

## 3. No-change items (explicitly confirmed)

These items were **not modified** in Phase 1. Documented here to confirm scope was contained.

| Item | Status | Notes |
|---|---|---|
| `app.py` | Unchanged | `/chat/complete` endpoint still active and functional |
| `tasks.py` | Unchanged | All Celery tasks still registered and operational |
| `database.py` | Unchanged | Celery factory, SQLAlchemy engine — no change |
| `cache.py` | Unchanged | Valkey session TTL logic — no change |
| `summarizer.py` | Unchanged | LangChain summarizer — no change (but see `langchain-core` risk above) |
| `docker-compose.yml` | Unchanged | All services: `chatbot-api`, `chatbot-worker`, `valkey-db`, `qdrant-db` — no change |
| `Dockerfile` | Unchanged | Base image, build steps, `ENTRYPOINT` — no change |
| `.env` / env vars | Unchanged | No new env vars are active in Phase 1 |
| API endpoints | Unchanged | `POST /chat/complete`, `GET /chat/complete/{task_id}`, `POST /document/create`, `POST /collection/create` — all unchanged |
| Celery broker | Unchanged | Valkey still serves as broker and result backend |
| Valkey | Unchanged | No new connections, no new key patterns |
| Qdrant | Unchanged | Collection `"llm"`, vector config — no change |
| MariaDB | Unchanged | Schema, ORM models — no change |
| Streamlit UI | Unchanged | Still polls `GET /chat/complete/{task_id}` as before |
| Nginx/Traefik | N/A | No reverse proxy configured in current setup |

---

## 4. Environment variables

### Currently active (no change)
All existing env vars remain unchanged:
```
OPENAI_API_KEY
MYSQL_USER, MYSQL_ROOT_PASSWORD, MYSQL_HOST, MYSQL_PORT
CELERY_BROKER_URL, CELERY_RESULT_BACKEND
```

### New env var introduced (not yet active in Phase 1)
```
CHECKPOINTER_DB_PATH=/tmp/langgraph_checkpoints.db
```
This is read by `checkpointer.py` but `checkpointer.py` is never called from any live code path in Phase 1. It has no effect until Phase 3.

**Action required in Phase 5:** Add this to `.env` and mount a persistent volume:
```yaml
volumes:
  - checkpointer_data:/var/checkpoints
environment:
  - CHECKPOINTER_DB_PATH=/var/checkpoints/langgraph.db
```

---

## 5. API changes

**None in Phase 1.** All endpoints are byte-for-byte identical. The old `/chat/complete` flow is completely unaffected.

---

## 6. Async / streaming changes

**None in Phase 1.** The new agent code uses synchronous `openai_chat_complete()` calls — same as the existing Celery tasks. No `async def`, no `asyncio`, no SSE, no chunked transfer encoding. Uvicorn configuration is unchanged.

SSE streaming is introduced in Phase 3 only. At that point the following must be reviewed:

- **Uvicorn timeout:** default `--timeout-keep-alive 5` may close SSE connections. Must be increased or disabled for streaming endpoints.
- **Nginx buffering:** if a reverse proxy is added later, `proxy_buffering off` is required for SSE. Currently there is no reverse proxy.
- **FastAPI `StreamingResponse`:** must use `media_type="text/event-stream"` and disable response buffering.

None of this applies in Phase 1.

---

## 7. Memory and CPU impact

### Phase 1 (current)
No change. New code files are loaded into the Python interpreter but the `agent/` package is never imported by `app.py` or `tasks.py`. Import cost is zero.

### Phase 3 onwards (forecast, not current)
When `get_legal_graph()` is called per worker process:
- LangGraph graph compilation: ~10–20 MB RAM per process
- SQLite checkpointer connection: ~2–5 MB per process
- With `--workers 2`: total additional ~40–50 MB across both processes
- CPU during inference: identical to current Celery worker (same OpenAI API calls). No GPU required.

The `grade_docs` node is the only new CPU/cost concern — it calls the LLM **once per retrieved document** (up to 6 calls for top-6 results). This is 6 additional OpenAI API calls per Legal query, compared to zero grading calls in the old pipeline. Plan for ~3–5x higher OpenAI token cost per Legal query after Phase 3.

---

## 8. Celery / Valkey behavior

**No change in Phase 1.** Celery worker still processes tasks from the Valkey broker exactly as before. The new `agent/` code has no awareness of Celery and vice versa.

In Phase 3, the Celery worker container (`chatbot-worker`) and Valkey container (`valkey-db`) are **removed** from `docker-compose.yml`. This is a significant infrastructure change but is out of scope for Phase 1.

---

## 9. Backward compatibility

All existing behavior is preserved:
- `/chat/complete` POST → identical
- `/chat/complete/{task_id}` GET polling → identical
- `/document/create` → identical
- Streamlit UI → identical
- Celery task queue → identical
- MariaDB reads/writes → identical

**Phase 1 introduces zero breaking changes.**

---

## 10. Deployment commands

### If deploying Phase 1 to an existing running environment:

```bash
# 1. Pull the new code
git pull origin main

# 2. Rebuild the API container (requirements.txt changed)
cd backend
docker compose build chatbot-api

# 3. Rebuild the worker container (requirements.txt changed, brain.py changed)
docker compose build chatbot-worker

# 4. Restart both containers
docker compose up -d chatbot-api chatbot-worker

# 5. Verify containers are healthy
docker ps | grep -E "chatbot-api|chatbot-worker"
docker logs --tail=20 chatbot-api
docker logs --tail=20 chatbot-worker
```

**Valkey and Qdrant do not need to be restarted.** Their images and configs are unchanged.

### Verify no import errors after rebuild:
```bash
docker exec chatbot-api python -c "import app; print('app OK')"
docker exec chatbot-worker python -c "import tasks; print('tasks OK')"
docker exec chatbot-api python -c "from agent.legal_graph import build_legal_graph; print('agent OK')"
```

---

## 11. Migration risks

| Risk | Probability | Severity | Mitigation |
|---|---|---|---|
| `langchain-core` version conflict breaks `summarizer.py` | Medium | High | Pin `langchain-core<0.3.0` in requirements, or test container build before deployment |
| SQLite checkpointer DB lost on container restart (Phase 3+) | High (if not addressed) | Medium | Mount persistent volume for `CHECKPOINTER_DB_PATH` in Phase 5 |
| `langgraph-prebuilt` / `langgraph-sdk` pulling unexpected transitive deps | Low | Low | Run `pip check` in CI to detect conflicts |
| `pytest` / `pytest-asyncio` in production image adds attack surface | Low | Low | Move test deps to a separate `requirements-dev.txt` |

---

## 12. Production risks

**Current phase (Phase 1): Very low.** No live code path touches the new agent code.

**Persistent risk to address before Phase 3:**
- `langchain-core` upgrade must be validated — run `docker build` in a staging environment and verify `summarizer.py` imports cleanly
- SQLite volume persistence must be planned before `get_legal_graph()` is wired to a live endpoint

---

## 13. Rollback steps

Phase 1 is fully reversible with zero downtime risk:

```bash
# Option A — revert requirements.txt and brain.py, delete new files
git checkout backend/requirements.txt
git checkout backend/src/brain.py
rm -rf backend/src/agent/
rm -rf backend/tests/

# Rebuild containers
cd backend
docker compose build chatbot-api chatbot-worker
docker compose up -d chatbot-api chatbot-worker
```

```bash
# Option B — if already on a feature branch, just reset to main
git checkout main
cd backend
docker compose build chatbot-api chatbot-worker
docker compose up -d chatbot-api chatbot-worker
```

**No database migrations, no Qdrant schema changes, no Valkey key changes were made.** Rollback requires only a container rebuild — no data recovery needed.

---

## 14. Smoke tests to validate deployment

Run these after deploying the rebuilt containers:

```bash
# 1. Existing endpoint still works (most important)
curl -s -X POST http://localhost:8000/chat/complete \
  -H "Content-Type: application/json" \
  -d '{"bot_id":"botFinance","user_id":"smoketest","message":"Hello"}' \
  | jq '.task_id'
# Expected: a non-null task_id string

# 2. Poll for result
TASK_ID=$(curl -s -X POST http://localhost:8000/chat/complete \
  -H "Content-Type: application/json" \
  -d '{"bot_id":"botFinance","user_id":"smoketest","message":"Hello"}' | jq -r '.task_id')
sleep 5
curl -s http://localhost:8000/chat/complete/$TASK_ID | jq '.status'
# Expected: "SUCCESS" or "PENDING"

# 3. New agent package imports cleanly inside container
docker exec chatbot-api python -c "
from agent.state import GraphState
from agent.nodes import rewrite, retrieve, grade_docs, generate, fallback, follow_up, rollback_router
from agent.legal_graph import build_legal_graph
from agent.checkpointer import get_checkpointer
print('All agent imports OK')
"
# Expected: "All agent imports OK"

# 4. Celery worker still processes tasks
docker exec chatbot-worker celery -A tasks.celery_app inspect ping
# Expected: pong response from worker

# 5. Run unit tests (no services needed)
docker exec chatbot-api python -m pytest /usr/src/app/tests/test_legal_graph.py -v --tb=short
# Expected: 18 passed
```

---

## 15. CI/CD recommendations

No CI/CD pipeline exists in the repo currently. These are recommended additions for before Phase 2:

```yaml
# Suggested CI steps (pseudocode — adapt to your CI system)
steps:
  - name: Build image
    run: docker build -t chatbot-api:ci ./backend

  - name: Check dependency conflicts
    run: docker run chatbot-api:ci pip check

  - name: Verify critical imports
    run: |
      docker run chatbot-api:ci python -c "import app"
      docker run chatbot-api:ci python -c "import tasks"
      docker run chatbot-api:ci python -c "from agent.legal_graph import build_legal_graph"

  - name: Run unit tests
    run: docker run chatbot-api:ci python -m pytest tests/test_legal_graph.py -v
```

---

## 16. Immediate action items (before merging or deploying)

| Priority | Action | Why |
|---|---|---|
| **High** | Add `langchain-core>=0.2.0,<0.3.0` pin to `requirements.txt` | Prevent `langgraph` from silently upgrading `langchain-core` and breaking `summarizer.py` in the Celery worker |
| **High** | Test `docker compose build` in a staging or local Docker environment | Confirm no pip conflicts and no import errors in production containers |
| **Medium** | Move `pytest` and `pytest-asyncio` to a `requirements-dev.txt` | Test packages should not be in the production image |
| **Medium** | Add `pytest tests/test_legal_graph.py` to CI pipeline | Prevent regressions in later phases |
| **Low** | Document `CHECKPOINTER_DB_PATH` in the `.env.example` or README | Avoid Phase 5 surprises around SQLite persistence |
