# Data Schemas & Retrieval

## Category Overview

This module covers all data persistence layers: the Qdrant vector database configuration, the MariaDB relational schema, the embedding model specification, and the document chunking metadata. The system uses a single Qdrant collection (`"llm"`) with 1536-dimensional DOT-product vectors matching the `text-embedding-3-large` output size. Relational state (conversations and raw documents) is stored in MariaDB via SQLAlchemy ORM. Conversation session identity is cached in Valkey (Redis) with a 360-second TTL.

---

## File Manifest

| File Path | Primary Role | Key Functions / Classes |
|---|---|---|
| `backend/src/vectorize.py` | Qdrant collection config, vector upsert/search | `create_collection()` — `VectorParams(size=1536, distance=Distance.DOT)`, `add_vector()`, `search_vector()` |
| `backend/src/brain.py` | Embedding model definition and invocation | `get_embedding(text, model="text-embedding-3-large")` — returns `List[float]` of size 1536 |
| `backend/src/models.py` | SQLAlchemy ORM schemas for `chat_conversations` and `document` tables | `ChatConversation`, `Document`, `insert_document()`, `update_chat_conversation()`, `get_conversation_messages()` |
| `backend/src/splitter.py` | Document chunking — defines chunk schema passed to Qdrant | `split_document()` — `TokenTextSplitter(chunk_size=100, chunk_overlap=10, separator=".")` with LlamaIndex `Document` metadata |
| `backend/src/configs.py` | Default collection name constant | `DEFAULT_COLLECTION_NAME = "llm"` |
| `backend/src/database.py` | SQLAlchemy engine and connection pool — MySQL DSN construction | `engine`, `SessionLocal`, `SQLALCHEMY_DATABASE_URL` |
| `backend/src/cache.py` | Valkey session schema — key format and TTL | `get_conversation_key()` → `"{bot_id}.{user_id}"`, TTL = 360s |
| `mariadb/init.sql` | Ground-truth SQL schema for both tables | `CREATE TABLE chat_conversations`, `CREATE TABLE document` |

---

## Schema Reference

### Qdrant Vector Collection
| Parameter | Value |
|---|---|
| Collection name | `"llm"` (from `configs.DEFAULT_COLLECTION_NAME`) |
| Vector size | `1536` (matches `text-embedding-3-large`) |
| Distance metric | `Distance.DOT` (dot product) |
| Point payload | `{"title": str, "content": str}` (chunk text + document title) |
| Client URL | `http://qdrant-db:6333` |

### MariaDB: `chat_conversations`
| Column | Type | Notes |
|---|---|---|
| `id` | `INT AUTO_INCREMENT PK` | |
| `conversation_id` | `VARCHAR(50)` | SHA-256 derived, from `utils.generate_request_id()` |
| `bot_id` | `VARCHAR(100)` | e.g. `"botFinance"` |
| `user_id` | `VARCHAR(100)` | |
| `message` | `TEXT` | Raw user input or summarized assistant response |
| `is_request` | `BOOLEAN` | `True` = user turn, `False` = assistant turn |
| `completed` | `BOOLEAN` | `True` when `is_request=False` |
| `created_at` / `updated_at` | `TIMESTAMP` | |

### MariaDB: `document`
| Column | Type | Notes |
|---|---|---|
| `id` | `INT AUTO_INCREMENT PK` | |
| `title` | `VARCHAR(200)` | Document title, also embedded alongside content |
| `content` | `TEXT` | Full raw document text |
| `created_at` / `updated_at` | `TIMESTAMP` | |

### Document Chunk Metadata (LlamaIndex)
| Field | Value |
|---|---|
| `chunk_size` | 100 tokens |
| `chunk_overlap` | 10 tokens |
| `separator` | `"."` (sentence boundary) |
| `metadata` | `{"course": "LLM"}` (default, passed to `Document`) |

### Valkey Session Key Schema
| Field | Value |
|---|---|
| Key format | `"{bot_id}.{user_id}"` |
| Value | SHA-256 hex string (33 chars) — the `conversation_id` |
| TTL | 360 seconds (sliding, refreshed on each access) |

---

## Ready-to-Use Command

```
/add backend/src/vectorize.py backend/src/brain.py backend/src/models.py backend/src/splitter.py backend/src/configs.py backend/src/database.py backend/src/cache.py mariadb/init.sql
```
