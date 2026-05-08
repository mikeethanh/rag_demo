# Tools and Functions

## Category Overview

This module documents all callable tool-like functions in the system: OpenAI API wrappers, embedding utilities, vector DB operations, and the document indexing pipeline. The current implementation does **not** use OpenAI function-calling / tool-use (`tools=` parameter), LangChain tools, or structured tool definitions. There are no Penalty Calculator tools, Web Search integrations (Tavily, SerpAPI, etc.), or external API wrappers beyond OpenAI and Qdrant. All "tools" are plain Python functions invoked directly inside Celery tasks.

---

## File Manifest

| File Path | Primary Role | Key Functions / Classes |
|---|---|---|
| `backend/src/brain.py` | OpenAI API wrapper — chat completions, embeddings, intent detection, prompt building | `openai_chat_complete(messages, model, raw)`, `get_embedding(text, model)`, `detect_user_intent(history, message)`, `gen_doc_prompt(docs)`, `generate_conversation_text(conversations)` |
| `backend/src/vectorize.py` | Qdrant client wrapper — collection management, vector upsert and similarity search | `create_collection(name)`, `add_vector(collection_name, vectors)`, `search_vector(collection_name, vector, limit)` |
| `backend/src/splitter.py` | LlamaIndex document chunking tool — tokenizes and splits raw text into overlapping nodes | `split_document(text, metadata)` |
| `backend/src/summarizer.py` | LangChain summarization tool — condenses assistant responses using `gpt-4o-mini` | `summarize_text(text)` |
| `backend/src/tasks.py` | Indexing pipeline — composes embedding + chunking + upsert into a single indexing operation | `index_document_v2(id, title, content, collection_name)` |
| `backend/src/cache.py` | Valkey (Redis) session tool — manages conversation ID lifecycle with TTL | `get_conversation_id(bot_id, user_id, ttl_seconds)`, `clear_conversation_id(bot_id, user_id)`, `get_conversation_key(bot_id, user_id)` |
| `backend/src/utils.py` | Utility functions — ID generation, logging setup | `generate_request_id(max_length)`, `generate_random_string(length)`, `setup_logging()` |

---

## Tool Inventory Detail

### OpenAI Wrappers (`brain.py`)
| Function | Model | Purpose |
|---|---|---|
| `openai_chat_complete()` | `gpt-4o-mini` (default) | General chat completion; used for both answer generation and intent detection |
| `get_embedding()` | `text-embedding-3-large` | Converts text to 1536-dim vector for Qdrant |
| `detect_user_intent()` | `gpt-4o-mini` (via `openai_chat_complete`) | Rephrases follow-up questions into standalone queries |

### Vector DB Tools (`vectorize.py`)
| Function | Backend | Parameters |
|---|---|---|
| `search_vector()` | Qdrant @ `http://qdrant-db:6333` | `limit=2` (top-K) in production calls from `tasks.py` |
| `add_vector()` | Qdrant upsert | `PointStruct(id, vector, payload)` where payload = `{title, content}` |

### Absent Tools (Extension Points)
- **Penalty Calculator**: no numeric/legal penalty computation exists; would be added as a new function in `brain.py` or a dedicated `calculators.py`
- **Web Search**: no Tavily / SerpAPI / DuckDuckGo integration; would require adding a search tool and wiring it into `bot_rag_answer_message` in `tasks.py`
- **OpenAI Function Calling**: `openai_chat_complete()` does not pass `tools=` or `tool_choice=`; adding structured tool use requires extending this function

---

## Ready-to-Use Command

```
/add backend/src/brain.py backend/src/vectorize.py backend/src/splitter.py backend/src/summarizer.py backend/src/tasks.py backend/src/cache.py backend/src/utils.py
```
