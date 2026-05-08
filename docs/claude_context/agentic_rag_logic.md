# Agentic RAG Logic

## Category Overview

This module describes the core RAG reasoning pipeline. The current implementation does **not** use LangGraph — it uses Celery tasks as a lightweight graph substitute. The pipeline is composed of three sequential steps that map conceptually to LangGraph nodes: (1) **Intent Rewriter** (`detect_user_intent`) rephrases follow-up questions into standalone queries, (2) **Retriever** (`search_vector`) fetches top-K relevant document chunks from Qdrant, and (3) **Generator** (`openai_chat_complete`) produces the final answer. There is no explicit Grader node or self-reflection loop: the system does not score retrieved documents, re-query on low relevance, or regenerate on hallucination. The summarizer (`summarize_text`) acts as a post-generation compressor before persisting to conversation history.

---

## File Manifest

| File Path | Primary Role | Key Functions / Classes |
|---|---|---|
| `backend/src/tasks.py` | Pipeline orchestrator — sequences Rewriter → Retriever → Generator → Summarizer | `llm_handle_message()`, `bot_rag_answer_message()`, `follow_up_question()`, `get_summarized_response()` |
| `backend/src/brain.py` | LLM and embedding wrappers — all OpenAI API calls | `detect_user_intent()` (Rewriter node), `openai_chat_complete()` (Generator node), `get_embedding()` (Retriever embedding), `gen_doc_prompt()` (context builder) |
| `backend/src/vectorize.py` | Retriever node — Qdrant vector search | `search_vector()`, `add_vector()`, `create_collection()` |
| `backend/src/summarizer.py` | Post-generation summarizer — compresses assistant response before saving to history | `summarize_text()` (uses LangChain `ChatOpenAI`) |
| `backend/src/splitter.py` | Document pre-processing — chunks raw text before indexing | `split_document()` (uses LlamaIndex `TokenTextSplitter`) |

---

## Pipeline Node Map (Conceptual)

```
User Message
    │
    ▼
[Node 1: Rewriter]  ←── brain.detect_user_intent(history, question)
    │  Rephrases follow-up question into standalone query
    ▼
[Node 2: Retriever] ←── brain.get_embedding() → vectorize.search_vector()
    │  top-2 Qdrant chunks with {title, content} payload
    ▼
[Node 3: Generator] ←── brain.openai_chat_complete(history + docs + question)
    │  gpt-4o-mini generates final answer
    ▼
[Node 4: Summarizer] ←── summarizer.summarize_text(response)
    │  Compresses response in Vietnamese for history storage
    ▼
Saved to MariaDB via models.update_chat_conversation()
```

**Missing nodes** (not implemented, candidates for future LangGraph upgrade):
- **Grader**: scores retrieved docs for relevance before generation
- **Hallucination checker**: verifies answer is grounded in retrieved docs
- **Re-query loop**: re-embeds with a rewritten query if retrieval score is low

---

## Ready-to-Use Command

```
/add backend/src/tasks.py backend/src/brain.py backend/src/vectorize.py backend/src/summarizer.py backend/src/splitter.py
```
