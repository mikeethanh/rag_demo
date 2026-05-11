# Phase 4 Retrospective — Architecture, AG-UI, and CopilotKit

---

## 1. What exactly is CopilotKit?

CopilotKit is a **React framework** (not a protocol, not a server) that makes it easier to build AI-powered UIs. It provides:

- A React **provider** (`<CopilotKit>`) that manages connection to an AI backend
- React **hooks** (`useCoAgent`, `useCopilotChat`) that expose agent state and chat functions
- Pre-built **UI components** (`<CopilotChat>`, `<CopilotSidebar>`) that render a full streaming chat interface
- A **runtime bridge** (the "CopilotKit Runtime") — a Node.js/Next.js middleware layer that sits between your frontend and your AI backend, handles authentication, tool routing, and event translation

Think of it as: **"the React layer for building AI chat UIs, with optional batteries included."**

---

## 2. How is CopilotKit related to this project?

Our project has a FastAPI backend that runs a LangGraph agent. CopilotKit was considered as the frontend framework because:

- Our backend already speaks **AG-UI** (the event protocol CopilotKit v2 is built on)
- CopilotKit's `<CopilotChat>` would give us a streaming chat UI for free
- CopilotKit's `useCoAgent` hook would give us live access to the LangGraph agent state (source documents, follow-up questions) without manual SSE parsing

**Why we did NOT use it in the end:**

CopilotKit v2's React components bundle **Tailwind CSS v4** internally. This project uses **Tailwind CSS v3**. When Next.js tries to process the CopilotKit v2 CSS through PostCSS, it crashes because Tailwind v4's `@layer base` syntax is not valid in a Tailwind v3 setup.

**What we use instead:** The `@ag-ui/client` package — the lower-level AG-UI client library that CopilotKit itself uses internally. We get the correct protocol implementation without the CSS conflict.

---

## 3. What is AG-UI? (Protocol vs Library)

AG-UI is two separate things that share a name:

### AG-UI as a Protocol

AG-UI is an **open event-streaming protocol** — a specification for how an AI agent backend should communicate with a frontend over HTTP. It defines:

- A set of **event types** sent as Server-Sent Events (SSE): `RUN_STARTED`, `TEXT_MESSAGE_CONTENT`, `STATE_DELTA`, `RUN_FINISHED`, `RUN_ERROR`, etc.
- A standard **HTTP endpoint shape**: `POST /runs` with a JSON body containing `thread_id`, `run_id`, `messages`, `state`
- How **state updates** work: via RFC-6902 JSON Patch deltas (`STATE_DELTA`) or full snapshots (`STATE_SNAPSHOT`)
- How **streaming text** works: start/content/end events for text and reasoning messages

Think of AG-UI the protocol like HTTP itself — it is just a contract. Any language can implement it.

### AG-UI as Python/JS Libraries

The AG-UI team also publishes **reference implementations** of the protocol:

| Package | Language | Role |
|---|---|---|
| `ag-ui-protocol` (or `ag_ui`) | Python | Server-side helpers: emit correct events, validate the protocol |
| `@ag-ui/client` | TypeScript/JS | Client-side: consume SSE streams, manage agent state |
| `@ag-ui/core` | TypeScript/JS | Shared types/interfaces (event types, message types) |

These libraries are **optional helpers**, not mandatory. The protocol itself is just JSON over HTTP.

---

## 4. What role does the AG-UI backend play?

Our FastAPI backend (`/runs` endpoint) is the **AG-UI server**. It:

1. Receives `POST /runs` with the user's message and thread ID
2. Runs the LangGraph agent
3. Streams AG-UI events back as SSE (`data: {...}\n\n` lines)

The backend is the **source of truth** for agent execution. It emits events that describe what is happening:

```
POST /runs  →  HTTP 200 (streaming)

data: {"type": "RUN_STARTED", "run_id": "abc"}
data: {"type": "TEXT_MESSAGE_START", "message_id": "m1"}
data: {"type": "TEXT_MESSAGE_CONTENT", "message_id": "m1", "delta": "Theo "}
data: {"type": "TEXT_MESSAGE_CONTENT", "message_id": "m1", "delta": "luật..."}
data: {"type": "STATE_DELTA", "delta": [{"op": "replace", "path": "/source_documents", "value": [...]}]}
data: {"type": "TEXT_MESSAGE_END", "message_id": "m1"}
data: {"type": "RUN_FINISHED"}
```

The frontend reads this stream and updates the UI as events arrive.

---

## 5. Why install AG-UI packages on the backend (pip install)?

The Python AG-UI package (`ag_ui` or `ag-ui-protocol`) provides:

- **Pre-built event emitters**: instead of writing `json.dumps({"type": "RUN_STARTED", ...})` manually, you call `emit_run_started(run_id=...)`
- **LangGraph integration**: a helper (`CopilotKitSDK` or similar) that automatically intercepts LangGraph `stream_mode="updates"` output and translates it into AG-UI events
- **Protocol validation**: ensures you don't emit malformed events (wrong field names, missing required fields)

### Are they required?

**No.** You can emit AG-UI events manually as plain JSON strings over SSE. The protocol is simple enough to implement by hand:

```python
# Fully manual — no AG-UI package needed
async def runs(request: Request):
    async def stream():
        yield f'data: {{"type": "RUN_STARTED", "run_id": "abc"}}\n\n'
        yield f'data: {{"type": "TEXT_MESSAGE_CONTENT", "delta": "Hello"}}\n\n'
        yield f'data: {{"type": "RUN_FINISHED"}}\n\n'
    return StreamingResponse(stream(), media_type="text/event-stream")
```

The AG-UI Python package saves time and reduces bugs, but is entirely optional.

---

## 6. What does `@ag-ui/client` actually provide on the frontend?

`@ag-ui/client` is the **TypeScript reference client** for the AG-UI protocol. It provides:

- **`HttpAgent`**: a class that wraps `fetch` + SSE parsing. You give it a URL and it handles the full request lifecycle
- **State management**: `agent.state` automatically accumulates `STATE_DELTA` patches into a single JS object
- **Message management**: `agent.messages` tracks the conversation history in AG-UI message format
- **`AgentSubscriber` interface**: typed callbacks (`onEvent`, `onRunFinalized`, `onRunFailed`, `onStateDeltaEvent`, etc.) so you write typed handlers instead of parsing raw JSON
- **Thread management**: `agent.threadId` persists the thread across calls
- **Abort/retry**: built-in cancellation of in-progress runs

In our project we use `HttpAgent` inside `useRAGAgent.ts`:

```typescript
const agent = new HttpAgent({ url: `${API_BASE}/runs`, threadId });
agent.addMessage({ id: randomUUID(), role: "user", content });
await agent.runAgent(undefined, {
  onEvent({ event }) { /* typed event handling */ },
  onRunFinalized() { /* set status=done */ },
  onRunFailed({ error }) { /* set status=error */ },
});
```

This replaces ~60 lines of manual SSE parsing we had in the old `useAGUI.ts`.

---

## 7. Tradeoffs: Full CopilotKit vs @ag-ui/client vs Custom SSE

| Concern | Full CopilotKit | @ag-ui/client only | Custom SSE |
|---|---|---|---|
| **Setup effort** | High (Runtime middleware required) | Low | Low |
| **Pre-built UI** | Yes (`<CopilotChat>`, sidebar, popup) | No | No |
| **Correct SSE parsing** | Yes | Yes | You write it |
| **State management** | Yes (React context) | Yes (agent.state) | You write it |
| **Thread persistence** | Yes | Yes (threadId on agent) | You write it |
| **Tailwind v4 conflict** | Yes (breaks our build) | No | No |
| **CopilotKit Runtime dependency** | Yes (extra server) | No | No |
| **Bundle size** | ~200KB+ | ~30KB | Minimal |
| **Protocol correctness** | Yes | Yes | Depends on your care |
| **Custom UI** | Possible but fights the defaults | Easy | Total control |
| **TypeScript types for events** | Yes | Yes (via @ag-ui/core) | You write them |

**For this project:** `@ag-ui/client` is the right choice. We want custom UI (Vietnamese UI, citations panel, CoT panel) and our backend speaks clean AG-UI. Full CopilotKit adds a required Runtime middleware layer and CSS conflicts with no benefit.

---

## 8. End-to-End Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER ACTION                             │
│              (types question, clicks follow-up chip)            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    REACT FRONTEND (Next.js)                     │
│                                                                 │
│  ChatPanel.tsx                                                  │
│  └── useRAGAgent(threadId)          ← custom hook              │
│      └── HttpAgent (from @ag-ui/client)                        │
│          • Holds thread_id, messages[], state{}                 │
│          • POST /runs → streams SSE                             │
│          • Calls onEvent() for each AG-UI event                │
│          • Updates React state → re-renders UI                  │
│                                                                 │
│  Components:                                                    │
│  ├── ChatInput.tsx     (user types, hits Enter)                │
│  ├── StepIndicator.tsx (shows current LangGraph node)          │
│  ├── CoTPanel.tsx      (reasoning/thinking text)               │
│  ├── AnswerBox.tsx     (streaming answer text)                 │
│  ├── Citations.tsx     (source_documents from STATE_DELTA)     │
│  └── FollowUpChips.tsx (follow_up_questions from STATE_DELTA)  │
└────────────────────────────┬────────────────────────────────────┘
                             │  POST /runs (HTTP + SSE)
                             │  {thread_id, messages, state}
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                  FASTAPI BACKEND (/runs endpoint)               │
│                                                                 │
│  Receives POST → starts LangGraph agent run                     │
│  Streams AG-UI events as SSE back to frontend                   │
│                                                                 │
│  Event types emitted:                                           │
│  • RUN_STARTED                                                  │
│  • REASONING_START / REASONING_MESSAGE_CONTENT / REASONING_END │
│  • TEXT_MESSAGE_START / TEXT_MESSAGE_CONTENT / TEXT_MESSAGE_END │
│  • STATE_DELTA  ← carries source_documents, follow_up_questions │
│  • RUN_FINISHED / RUN_ERROR                                     │
└────────────────────────────┬────────────────────────────────────┘
                             │  LangGraph stream_mode="updates"
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                       LANGGRAPH AGENT                           │
│                                                                 │
│  Nodes (each node = one step, emits STATE_DELTA when done):    │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────────┐  │
│  │ classify │ → │ retrieve │ → │ generate │ → │ follow_up  │  │
│  └──────────┘   └──────────┘   └──────────┘   └────────────┘  │
│       ↕               ↕               ↕               ↕        │
│  branch=legal   source_docs     answer text    follow_ups       │
│  branch=fallback                                                │
│                                                                 │
│  State shape:                                                   │
│  {                                                              │
│    query: string,                                               │
│    branch: "legal" | "fallback",                               │
│    source_documents: [{content, source, page}],                │
│    follow_up_questions: string[],                               │
│    messages: [...]                                              │
│  }                                                              │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     SUPPORTING SERVICES                         │
│                                                                 │
│  Qdrant      ← vector search for source_documents              │
│  OpenAI API  ← embeddings (text-embedding-3-large) + GPT-4o   │
│  Valkey      ← Celery broker (if using async task queue)       │
│  MariaDB     ← conversation history (if persisting threads)    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 9. Which parts are what?

| Component | Type | Optional? | Why / Why Not |
|---|---|---|---|
| **AG-UI event format** | Protocol (spec) | No — it's the contract | Frontend and backend must agree on event shapes |
| **`@ag-ui/client` (HttpAgent)** | Library | Yes | Could write SSE parsing manually; saves ~60 lines |
| **`@ag-ui/core`** | Library (types only) | Yes | TypeScript types for events; use `Record<string, unknown>` without it |
| **`ag_ui` Python package** | Library | Yes | Could emit raw JSON strings over SSE manually |
| **CopilotKit Runtime** | Server middleware | Yes | Only needed if using CopilotKit's GraphQL-based hooks (useCoAgent v1) |
| **`@copilotkit/react-core`** | React framework | Yes | Adds provider + hooks on top of AG-UI; we bypass it |
| **`@copilotkit/react-ui`** | React UI components | Yes | Pre-built chat UI; conflicts with our Tailwind v3 |
| **LangGraph** | Python graph framework | No — it's our agent logic | This is where the AI reasoning lives |
| **FastAPI `/runs` endpoint** | Server code | No | Must exist; this is what serves the agent |
| **SSE (text/event-stream)** | Transport protocol | No | How events get from backend to frontend |
| **`useRAGAgent.ts`** | Our custom hook | No (in current arch) | Wraps HttpAgent with React state management |
| **React components** | UI layer | No | How we render the UI |

---

## 10. What is actually necessary for this project?

**Minimum required:**

1. FastAPI endpoint that emits AG-UI events over SSE (the protocol)
2. LangGraph agent that does retrieval and generation
3. A frontend that reads the SSE stream and updates the UI

**Everything else is a quality-of-life choice:**

- `@ag-ui/client` → saves writing SSE parser + state accumulator
- `ag_ui` Python package → saves writing event emitters manually
- CopilotKit React components → saves building chat UI from scratch (but conflicts with our setup)
- Custom components (Citations, CoTPanel, etc.) → we built these for Vietnamese UX

**The architecture we ended up with is the minimum correct implementation:**

```
LangGraph agent
  → FastAPI emits AG-UI events as SSE
    → HttpAgent (from @ag-ui/client) parses events
      → useRAGAgent hook manages React state
        → Custom React components render the UI
```

No CopilotKit Runtime. No GraphQL. No extra middleware. Just HTTP + SSE + typed events.

---

## 11. Why the CopilotKit exploration was still valuable

Even though we didn't use CopilotKit's React layer, researching it clarified:

- The AG-UI protocol spec (CopilotKit's docs are the best reference for it)
- The `HttpAgent` API from `@ag-ui/client` (CopilotKit uses it internally)
- The `AgentSubscriber` interface (the typed callback system we use in `useRAGAgent`)
- The `selfManagedAgents` pattern (how to point any AG-UI agent at the right URL)

The insight: **CopilotKit is the framework; AG-UI is the protocol; our project only needs the protocol + the reference client.**
