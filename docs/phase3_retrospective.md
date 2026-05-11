# Phase 3 Retrospective — AG-UI SSE Server

---

## What Phase 3 accomplished

Phase 3 wired the LangGraph `main_graph` built in Phase 2 to a live FastAPI streaming endpoint (`POST /runs`). Responses now flow as a typed Server-Sent Events stream following the AG-UI protocol, using the official `ag-ui-protocol` Python package — no hand-rolled SSE.

The old `/chat/complete` Celery polling endpoint is still present and fully operational. Phase 3 adds the new streaming path without removing anything, so the existing Streamlit frontend continues to work unchanged.

---

## Files created

### `backend/src/server/__init__.py`
Empty package init. Required so `server.agui_handler` is importable as a package.

---

### `backend/src/server/agui_handler.py`
**Purpose:** Translates LangGraph `stream_mode="updates"` output into a typed AG-UI SSE stream.

**Key design decisions:**

| Decision | Rationale |
|---|---|
| Uses `ag-ui-protocol` package (`ag_ui.core`) | User requirement — not hand-rolled |
| Events serialized via `.model_dump_json()` | Official Pydantic serialization from the package; produces correct `type` enum values |
| SSE format: `f"data: {event.model_dump_json()}\n\n"` | Standard SSE line format; FastAPI `StreamingResponse` sends it as-is |
| `stream_mode="updates"` | LangGraph yields `{node_name: state_delta}` per completed node — clean mapping to `StateDeltaEvent` |
| One `StateDeltaEvent` per node | Each node's full output becomes an RFC-6902 `replace` patch list |
| `TEXT_MESSAGE_*` events only for text-producing nodes | `_TEXT_NODES = {generate, fallback, general_answer, web_search_answer, calculation_answer}` — routing/retrieval nodes emit only `STATE_DELTA` |
| Word-by-word `TextMessageContentEvent` | Simulates streaming without true token-level streaming from OpenAI (no LangChain streaming needed at this stage) |
| `RunErrorEvent` on exception | Graph failures yield one error event then return cleanly — no 500 response, stream stays open until the error event lands |

**Event sequence per request:**
```
RUN_STARTED
  [per LangGraph node]:
    STATE_DELTA
    [if text node]: TEXT_MESSAGE_START / TEXT_MESSAGE_CONTENT... / TEXT_MESSAGE_END
RUN_FINISHED  (or RUN_ERROR if exception)
```

---

### `backend/tests/test_agui_server.py`
**Purpose:** Phase 3 test gate. 11 tests across 2 classes.

| Class | Tests | What is verified |
|---|---|---|
| `TestAguiEventStream` | 8 | Event ordering (RUN_STARTED first, RUN_FINISHED last); STATE_DELTA emitted for every node; TEXT_MESSAGE events only for text nodes; content delta joined equals full generation; run_id shared between RUN_STARTED and RUN_FINISHED; RUN_ERROR emitted on graph exception |
| `TestRunsEndpoint` | 3 | POST /runs returns 200 text/event-stream; missing thread_id returns 422; empty query returns 400 |

---

## Files modified

### `backend/src/app.py`
**Changes:**
1. Added imports: `from agent.checkpointer import get_checkpointer`, `from server.agui_handler import agui_event_stream`
2. Added `RunRequest` Pydantic model: `thread_id: str`, `query: str`, `run_id: Optional[str]`
3. Added `POST /runs` endpoint — calls `get_main_graph()`, builds initial state, returns `StreamingResponse`

**Response headers on `/runs`:**
```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no   ← disables nginx buffering in Docker
```

**All existing endpoints unchanged:**
- `GET /`
- `POST /chat/complete` (Celery polling path)
- `GET /chat/complete/{task_id}`
- `POST /collection/create`
- `POST /document/create`

---

### `backend/requirements.txt`
Added: `ag-ui-protocol==0.1.18`

---

## Test gate result

```
pytest backend/tests/test_agui_server.py -v
======================== 11 passed in 4.62s ========================
```

Full suite (Phase 1 + Phase 2 + Phase 3):
```
pytest backend/tests/ -v
======================== 63 passed in 4.63s ========================
```

---

## Bugs encountered and fixed

### Bug 1 — Hand-rolled SSE rejected; must use `ag-ui-protocol`

**Problem:** First draft of `agui_handler.py` implemented SSE by hand using `json.dumps` and `f"data: {data}\n\n"` strings with manually constructed dicts (`{"type": "RUN_STARTED", ...}`).

**User correction:** "i see that you write from scratch, and that is not what i want, u can use `pip install ag-ui-protocol`"

**Fix:** Installed `ag-ui-protocol==0.1.18` into the `rag_demo` conda env, introspected `ag_ui.core` to find the correct typed event classes and `.model_dump_json()` serialization, and rewrote the handler from scratch using the package.

**Key discovery during introspection:** The `EventType` enum values are uppercase strings (e.g. `"RUN_STARTED"`, `"STATE_DELTA"`) — the package handles this automatically when you instantiate the typed classes, so no manual enum mapping is needed.

---

### Bug 2 — Endpoint tests failed due to `summarizer.py` module-level `ChatOpenAI` instantiation

**Problem:** `TestClient(app)` triggers the full import chain: `app.py` → `tasks.py` → `summarizer.py`, which instantiates `ChatOpenAI(model_name="gpt-4o-mini")` at module level. Without `OPENAI_API_KEY` in the test environment, this raises `pydantic.v1.error_wrappers.ValidationError`.

**Fix:** In the `test_client` fixture, set `os.environ.setdefault("OPENAI_API_KEY", "sk-test-placeholder")` and mock `langchain.chat_models.ChatOpenAI` before importing `app`. Also clear cached module entries from `sys.modules` so the patched environment takes effect on reimport.

**Note:** This is a pre-existing `summarizer.py` design issue (module-level instantiation with external API dependency) carried from Phase 1. It only surfaces in test isolation without Docker env vars. The fix is local to the test fixture — `summarizer.py` is unchanged.

---

### Bug 3 — MariaDB connection attempted during TestClient import

**Problem:** `models.py` (imported by `app.py`) uses SQLAlchemy at module level. `TestClient` import triggered a real TCP connection attempt to MariaDB (`0.0.0.0:3308`), which is not running in the test environment.

**Fix:** Mocked `sqlalchemy.create_engine` in the test fixture before importing `app`. Combined with the `OPENAI_API_KEY` mock above, this allows `TestClient` to import and initialize the FastAPI app without any real external services.

---

## Dependencies added

| Package | Version | Why |
|---|---|---|
| `ag-ui-protocol` | `0.1.18` | Official AG-UI typed event classes for the SSE stream |

Added to `requirements.txt` and installed in `rag_demo` conda env.

---

## What Phase 3 does NOT change

| Item | Status |
|---|---|
| Celery / Valkey | Fully operational — `POST /chat/complete` still dispatches Celery tasks |
| Streamlit UI | Unchanged — still polls `GET /chat/complete/{task_id}` |
| MariaDB schema | Unchanged |
| Qdrant collection | Unchanged |
| Phase 1 + Phase 2 tests | All 52 still pass (63 total with Phase 3) |
| `agent/` code | Unchanged — `main_graph.py` is now called from a live endpoint but its internals are identical to Phase 2 |
