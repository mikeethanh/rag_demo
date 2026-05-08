# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Demo RAG chatbot system with the following components:
- **backend/** — FastAPI API + Celery worker, Valkey (Redis-compatible) queue/cache, Qdrant vector DB
- **chatbot-ui/** — Streamlit frontend
- **mariadb/** — MariaDB for conversation history and documents

All services communicate over a shared Docker network (`internal-network`).

## Running the system

Create the shared network first (one-time):
```
docker network create internal-network
```

Start each component:
```bash
cd backend && docker compose up -d --build
cd chatbot-ui && docker compose up -d --build
cd mariadb && docker compose up -d
```

Ports: API `8000`, UI `8051`, Valkey `6379`, Qdrant `6333/6334`, MariaDB `3308→3306`.

Logs:
```bash
docker logs -f chatbot-api
docker logs -f chatbot-worker
```

## Architecture

### Request flow

1. UI (`chat_interface.py`) POSTs to `/chat/complete` → gets back a `task_id`
2. API (`app.py`) dispatches a Celery task (`llm_handle_message`) to Valkey broker
3. Worker (`tasks.py`) processes the task:
   - Saves user message to MariaDB via `models.py`
   - Loads conversation history; calls `detect_user_intent` to rephrase follow-up questions
   - Embeds the rephrased question via OpenAI `text-embedding-3-large`
   - Searches Qdrant collection `"llm"` for top-2 relevant document chunks
   - Calls OpenAI `gpt-4o-mini` with history + retrieved docs + question
   - Summarizes the response, saves to MariaDB
4. UI polls `GET /chat/complete/{task_id}` until status != `PENDING`

### Key modules (backend/src/)

| File | Role |
|---|---|
| `app.py` | FastAPI routes: `/chat/complete`, `/document/create`, `/collection/create` |
| `tasks.py` | Celery tasks: `llm_handle_message`, `bot_rag_answer_message`, `index_document_v2` |
| `brain.py` | OpenAI wrappers: chat completion, embeddings, intent detection |
| `vectorize.py` | Qdrant operations: create collection, upsert/search vectors (size=1536, DOT distance) |
| `models.py` | SQLAlchemy ORM: `ChatConversation`, `Document`; conversation CRUD |
| `database.py` | SQLAlchemy engine + Celery app factory; reads env vars for MySQL/Redis |
| `cache.py` | Valkey (Redis) session management: maps `(bot_id, user_id)` → `conversation_id` with 360s TTL |
| `splitter.py` | Document chunking via LlamaIndex before indexing |
| `summarizer.py` | Summarizes assistant responses before saving to conversation history |
| `configs.py` | `DEFAULT_COLLECTION_NAME = "llm"` |

### Conversation session management

Conversation IDs are stored in Valkey with a 360-second TTL keyed by `{bot_id}.{user_id}`. A new conversation starts automatically when the key expires.

### Document indexing

`POST /document/create` saves raw content to MariaDB, then splits it into chunks (`splitter.py`), embeds each chunk, and upserts into Qdrant with `{title, content}` payload.

## Environment variables

Backend reads from `backend/env` (rename to `.env` for Docker Compose). Key vars:
- `OPENAI_API_KEY`
- `MYSQL_USER`, `MYSQL_ROOT_PASSWORD`, `MYSQL_HOST`, `MYSQL_PORT`
- `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` (default: `redis://localhost:6379`)

## Qdrant dashboard

http://localhost:6333/dashboard#/collections/llm
