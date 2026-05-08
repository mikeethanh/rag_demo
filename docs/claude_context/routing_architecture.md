# Routing Architecture

## Category Overview

This module covers all request entry points, message dispatching logic, and task routing within the system. The API layer (`app.py`) acts as the HTTP gateway, receiving user messages and dispatching them as asynchronous Celery tasks via Valkey (Redis). The worker layer (`tasks.py`) routes each message through the RAG pipeline. There are no semantic routers or domain-specific branches (General / Legal / Calculations) in the current implementation — all messages follow a single unified pipeline: intent detection → embedding → vector search → LLM generation. The `bot_id` field in `CompleteRequest` is the only hook available for future multi-bot routing.

---

## File Manifest

| File Path | Primary Role | Key Functions / Classes |
|---|---|---|
| `backend/src/app.py` | HTTP API gateway — exposes REST endpoints, validates requests, dispatches Celery tasks | `CompleteRequest` (Pydantic model), `complete()` POST handler, `get_response()` polling handler, `create_document()`, `create_vector_collection()` |
| `backend/src/tasks.py` | Celery task dispatcher — orchestrates the full RAG pipeline per message | `llm_handle_message` (entry Celery task), `bot_rag_answer_message` (RAG task), `index_document_v2` (indexing pipeline), `follow_up_question()` |
| `backend/src/database.py` | Celery app factory and DB engine factory — wires broker/backend URLs | `get_celery_app()`, `get_db()`, `SessionLocal` |
| `backend/src/configs.py` | Global constants used as routing parameters | `DEFAULT_COLLECTION_NAME = "llm"` |
| `chatbot-ui/chat_interface.py` | Streamlit UI — initiates requests and polls for results; hardcodes `bot_id = "botFinance"` | `send_user_request()`, `get_bot_response()`, `get_chat_complete()`, `response_generator()` |

---

## Architecture Notes

- **Sync vs Async routing**: `POST /chat/complete` supports both modes via the `sync_request` flag. When `False` (default), `llm_handle_message.delay()` enqueues the task; the UI polls `GET /chat/complete/{task_id}` up to 60 seconds.
- **No semantic router present**: All messages converge on `bot_rag_answer_message`. Branching by domain (Legal, Financial, Calculations) would require adding a classifier before `bot_rag_answer_message` in `tasks.py:llm_handle_message`.
- **Bot identity**: `bot_id` flows from UI → API → Celery task → `update_chat_conversation` → `get_conversation_id(bot_id, user_id)`, allowing future per-bot routing.

---

## Ready-to-Use Command

```
/add backend/src/app.py backend/src/tasks.py backend/src/database.py backend/src/configs.py chatbot-ui/chat_interface.py
```
