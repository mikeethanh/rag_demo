# Migration Plan — LangGraph + AG-UI

## Decisions locked in

| Decision | Choice |
|---|---|
| Celery worker | Replaced entirely by LangGraph |
| API endpoint | `/chat/complete` replaced by AG-UI streaming (`POST /runs`) |
| Frontend | Next.js — minimal chat UI only |
| Router branches | All 4: Legal, General, Calculation (stub), Ambiguous (Tavily) |
| Penalty Calculator | Placeholder interface, no real math |
| Web Search | Tavily (`TAVILY_API_KEY`) |
| Conversation history | LangGraph checkpointer only — MariaDB dropped for chat history |
| MariaDB | Kept only for document storage (`document` table) |

---

## Phase Overview

```
Phase 1 → LangGraph Legal RAG core (graph + nodes + checkpointer)
Phase 2 → Semantic Router + remaining branches (General, Calculation, Web Search)
Phase 3 → AG-UI server (FastAPI /runs SSE endpoint)
Phase 4 → Next.js frontend (chat UI consuming AG-UI events)
Phase 5 → Docker integration + full system smoke test
```

Each phase ends with a **test gate**. The next phase does NOT start until that gate passes.

---

## Phase 1 — LangGraph Legal RAG Core

### Goal
Replace `tasks.py` + `brain.py` pipeline with a proper LangGraph graph for the Legal branch.

### Scope
- New file: `backend/src/agent/legal_graph.py`
- New file: `backend/src/agent/state.py` — `GraphState` TypedDict
- New file: `backend/src/agent/nodes.py` — all 5 nodes + fallback
- New file: `backend/src/agent/checkpointer.py` — SQLite checkpointer setup
- Reuse: `vectorize.py` (Qdrant search), `brain.py` (embeddings, chat completions)

### GraphState

```python
class GraphState(TypedDict):
    query: str
    documents: list
    generation: str
    transformation_count: int
    follow_up_questions: list
    source_documents: list   # {title, source, page}
```

### Nodes to implement

| Node | Logic |
|---|---|
| `rewrite` | CoT prompt → LLM rewrites query; `transformation_count += 1` |
| `retrieve` | embed query → Qdrant top-6 → `documents` + `source_documents` |
| `grade_docs` | LLM scores each doc 0-1; `avg_score`; conditional edge |
| `generate` | system + history + docs + query → cited answer |
| `follow_up` | query + docs + generation → 3 follow-up questions |
| `fallback` | LLM-only answer, no citations; reached when count ≥ 3 |

### Conditional edge — Rollback Guard

```
grade_docs → avg_score >= 0.7       → generate
grade_docs → avg_score < 0.7 AND transformation_count < 3  → rewrite
grade_docs → avg_score < 0.7 AND transformation_count >= 3 → fallback
```

### Test gate — Phase 1 ✅

```bash
cd backend
python -m pytest tests/test_legal_graph.py -v
```

Tests must cover:
- [ ] Graph compiles and runs end-to-end with a mock LLM
- [ ] `grade_docs` routes to `generate` when avg_score >= 0.7
- [ ] `grade_docs` routes to `rewrite` when avg_score < 0.7 and count < 3
- [ ] `grade_docs` routes to `fallback` when count >= 3
- [ ] Final state contains `generation`, `source_documents`, `follow_up_questions`
- [ ] Checkpointer persists state between invocations (thread_id continuity)

---

## Phase 2 — Semantic Router + All Branches

### Goal
Add a classifier that routes queries into 4 branches before hitting the Legal graph.

### Scope
- New file: `backend/src/agent/router.py` — `SemanticRouter` using LLM classifier
- New file: `backend/src/agent/branches/general.py` — direct gpt-4o-mini call
- New file: `backend/src/agent/branches/calculation.py` — stub tools
- New file: `backend/src/agent/branches/web_search.py` — Tavily agent
- New file: `backend/src/agent/main_graph.py` — top-level graph wiring all branches
- New env var: `TAVILY_API_KEY`

### Router logic

The router is a LangGraph node that calls `gpt-4o-mini` with a classification prompt:

```
Given this user query, classify it into exactly one category:
- LEGAL: questions about laws, regulations, statutes, legal rights, penalties
- GENERAL: general knowledge, greetings, small talk, factual questions
- CALCULATION: requests to compute a penalty amount, fine, or numerical legal result
- AMBIGUOUS: unclear, needs web search for current events or unknown topics

Query: {query}
Respond with one word only.
```

### Branch implementations

**General branch**: `gpt-4o-mini` call with system prompt, no retrieval. Returns `{generation}`.

**Calculation branch (stub)**:
- Tool 1 — `penalty_calculator(offense_type, base_amount)` → returns `base_penalty: float`
- Tool 2 — `apply_factors(base_penalty, mitigating_factors)` → returns `final_penalty: float`
- Both tools return hardcoded/random values with realistic field names
- LLM calls tools via OpenAI function calling, formats result into a sentence

**Web Search branch (Tavily)**:
- Node 1: Query rewriter — optimizes query for web search
- Node 2: Tavily search — returns top-5 results
- Node 3: Result ranker + summarizer — LLM summarizes and ranks results
- Returns `{generation, sources: list[url]}`

### Response Aggregator

All branches normalize output to:
```python
{
    "generation": str,           # the answer
    "source_documents": list,    # [] for General/Calculation, URLs for WebSearch, chunks for Legal
    "follow_up_questions": list, # [] for non-Legal branches
    "branch": str,               # "legal" | "general" | "calculation" | "web_search"
}
```

### Test gate — Phase 2 ✅

```bash
cd backend
python -m pytest tests/test_router.py tests/test_branches.py -v
```

Tests must cover:
- [ ] Router correctly classifies legal / general / calculation / ambiguous queries (mock LLM)
- [ ] General branch returns a `generation` string
- [ ] Calculation branch calls both tools in sequence and returns formatted result
- [ ] Web Search branch (mocked Tavily) returns `generation` + `sources`
- [ ] All branches return normalized aggregator schema
- [ ] Main graph routes correctly end-to-end for each branch type

---

## Phase 3 — AG-UI Server (FastAPI `/runs` SSE endpoint)

### Goal
Expose the LangGraph `main_graph` via the AG-UI protocol over Server-Sent Events.

### Scope
- Remove: `app.py` routes `/chat/complete` (POST + GET polling)
- Remove: all Celery task dispatch code, `database.py` Celery factory
- Remove: `celery` and `valkey` from `docker-compose.yml`
- Add: `backend/src/server/agui_handler.py` — AG-UI event emitter
- Modify: `backend/src/app.py` — new `POST /runs` endpoint returning `text/event-stream`
- Add env var: `CHECKPOINTER_DB_PATH` (SQLite file path for LangGraph checkpointer)

### Event sequence emitted per node

```
RUN_STARTED           → { runId, threadId }
--- for each node: ---
STATE_DELTA           → RFC-6902 patch of GraphState changes
REASONING_START       → (if node has CoT LLM call)
REASONING_MESSAGE_CONTENT ×N  → streamed tokens
REASONING_END
TEXT_MESSAGE_START    → (on Generate / Fallback node)
TEXT_MESSAGE_CONTENT ×N       → streamed answer tokens
TEXT_MESSAGE_END
--- end ---
RUN_FINISHED          → { outcome: "success" | "fallback" }
```

### API contract

**Request:**
```
POST /runs
Content-Type: application/json

{
  "thread_id": "string",      // conversation session ID (replaces bot_id+user_id)
  "user_id": "string",
  "input": [{ "role": "user", "content": "..." }],
  "state": {}                 // optional: initial state override
}
```

**Response:**
```
HTTP 200
Content-Type: text/event-stream

data: {"type": "RUN_STARTED", ...}
data: {"type": "STATE_DELTA", ...}
...
data: {"type": "RUN_FINISHED", ...}
```

### Document endpoints (kept)
`POST /document/create` and `POST /collection/create` remain unchanged — MariaDB `document` table is still used.

### Test gate — Phase 3 ✅

```bash
cd backend
python -m pytest tests/test_agui_server.py -v
# Then manual smoke test:
curl -N -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"t1","user_id":"u1","input":[{"role":"user","content":"What is the penalty for tax evasion?"}]}'
```

Tests must cover:
- [ ] `POST /runs` returns `Content-Type: text/event-stream`
- [ ] Event stream starts with `RUN_STARTED` and ends with `RUN_FINISHED`
- [ ] `STATE_DELTA` events are valid RFC-6902 JSON patches
- [ ] `TEXT_MESSAGE_CONTENT` events arrive token-by-token (streaming, not batched)
- [ ] Same `thread_id` across two requests shares checkpointed state (memory works)
- [ ] `POST /document/create` still works (no regression)

---

## Phase 4 — Next.js Frontend (Minimal Chat UI)

### Goal
Replace Streamlit with a Next.js app that consumes the AG-UI SSE stream.

### Scope
- New directory: `chatbot-ui-next/` (alongside existing `chatbot-ui/`)
- Stack: Next.js 14 (App Router), TypeScript, Tailwind CSS
- Dependency: `ag-ui-protocol` npm package (if available) or hand-rolled SSE consumer
- No auth, no user management

### UI components

| Component | Behavior |
|---|---|
| **Chat input** | Text box + send button; on submit → `POST /runs` |
| **Step indicator** | Shows current node name (Rewrite / Retrieve / Grade / Generate) |
| **CoT panel** | Expandable panel that streams `REASONING_MESSAGE_CONTENT` tokens |
| **Answer box** | Streams `TEXT_MESSAGE_CONTENT` tokens in real-time |
| **Citations** | Renders `source_documents` as a list below the answer |
| **Follow-up chips** | Clickable buttons from `follow_up_questions`; click → new POST /runs |
| **Fallback indicator** | Badge shown when `RUN_FINISHED.outcome == "fallback"` |

### State management
- React `useReducer` consuming SSE events, patching local state with RFC-6902 patches
- `thread_id` stored in `localStorage` for session continuity

### Test gate — Phase 4 ✅

```bash
cd chatbot-ui-next
npm run build   # must compile with 0 TypeScript errors
npm run test    # unit tests for SSE event reducer
```

Manual smoke test checklist:
- [ ] Sending a legal query streams the answer token-by-token
- [ ] Step indicator updates as each node runs
- [ ] CoT panel shows reasoning text
- [ ] Citations appear after answer completes
- [ ] Follow-up chips appear; clicking one starts a new run
- [ ] Fallback badge appears when the fallback node fires
- [ ] Same browser session reuses `thread_id` (history maintained)

---

## Phase 5 — Docker Integration + Full System Smoke Test

### Goal
Wire all new services into Docker Compose and run a full end-to-end test.

### Changes
- `backend/docker-compose.yml`:
  - Remove: `valkey` service, `celery-worker` service
  - Modify: `chatbot-api` — remove Celery env vars, add `TAVILY_API_KEY`, `CHECKPOINTER_DB_PATH`
  - Add: volume mount for SQLite checkpointer DB
- `chatbot-ui-next/docker-compose.yml`: new Next.js service on port `3000`
- `backend/env` (rename to `.env`): add `TAVILY_API_KEY`

### Services after migration

| Service | Port | Role |
|---|---|---|
| `chatbot-api` | 8000 | FastAPI + LangGraph (no Celery) |
| `chatbot-ui-next` | 3000 | Next.js frontend |
| `qdrant-db` | 6333/6334 | Vector store |
| `mariadb` | 3308 | Document storage only |
| ~~`valkey`~~ | ~~6379~~ | Removed |
| ~~`celery-worker`~~ | — | Removed |

### Test gate — Phase 5 ✅

```bash
docker network create internal-network
cd backend && docker compose up -d --build
cd chatbot-ui-next && docker compose up -d --build
cd mariadb && docker compose up -d

# Automated smoke test
python tests/e2e/test_smoke.py
```

E2E smoke test covers:
- [ ] All containers healthy (`docker ps`)
- [ ] `POST /runs` with a legal query completes and returns `RUN_FINISHED`
- [ ] `POST /runs` with a general query completes correctly
- [ ] `POST /runs` with a calculation query returns a penalty result
- [ ] `POST /runs` with an ambiguous query returns web search results
- [ ] `POST /document/create` still indexes a document to Qdrant + MariaDB
- [ ] Two requests with same `thread_id` show conversation memory

---

## File structure after migration

```
backend/
  src/
    agent/
      state.py              # GraphState TypedDict
      nodes.py              # rewrite, retrieve, grade_docs, generate, follow_up, fallback
      legal_graph.py        # LangGraph graph for Legal branch
      router.py             # SemanticRouter node
      main_graph.py         # top-level graph wiring all branches
      checkpointer.py       # SQLite checkpointer factory
      branches/
        general.py
        calculation.py
        web_search.py
    server/
      agui_handler.py       # AG-UI event emitter
    app.py                  # FastAPI: POST /runs, POST /document/create, POST /collection/create
    vectorize.py            # unchanged
    brain.py                # unchanged (reused by nodes)
    models.py               # unchanged (document table only)
    database.py             # trimmed (no Celery factory)
    splitter.py             # unchanged
    configs.py              # updated constants
  tests/
    test_legal_graph.py     # Phase 1 gate
    test_router.py          # Phase 2 gate
    test_branches.py        # Phase 2 gate
    test_agui_server.py     # Phase 3 gate
    e2e/test_smoke.py       # Phase 5 gate
chatbot-ui-next/            # Phase 4
  src/app/
    page.tsx                # main chat UI
    components/
      ChatInput.tsx
      StepIndicator.tsx
      CoTPanel.tsx
      AnswerBox.tsx
      Citations.tsx
      FollowUpChips.tsx
  hooks/
    useAGUI.ts              # SSE consumer + state reducer
  docker-compose.yml
```
