import logging
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from utils import setup_logging
from models import insert_document
from vectorize import create_collection, add_vector, search_vector
from brain import get_embedding
from splitter import split_document
from configs import DEFAULT_COLLECTION_NAME
from agent.main_graph import get_main_graph
from server.agui_handler import agui_event_stream

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class MessageInput(BaseModel):
    role: str
    content: Any  # AG-UI sends string or list of content blocks
    id: Optional[str] = None


class RunRequest(BaseModel):
    # AG-UI client sends camelCase; support both forms
    threadId: Optional[str] = None
    thread_id: Optional[str] = None
    runId: Optional[str] = None
    messages: Optional[List[MessageInput]] = None
    input: Optional[List[MessageInput]] = None
    state: Optional[Dict[str, Any]] = None
    tools: Optional[List[Any]] = None
    context: Optional[List[Any]] = None
    forwardedProps: Optional[Dict[str, Any]] = None


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.post("/runs")
async def run_agent(data: RunRequest):
    thread_id = data.threadId or data.thread_id
    if not thread_id:
        raise HTTPException(status_code=400, detail="threadId is required")

    all_messages = data.messages or data.input or []
    user_messages = [m for m in all_messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="input must contain at least one user message")

    raw_content = user_messages[-1].content
    query = raw_content if isinstance(raw_content, str) else " ".join(
        c.get("text", "") for c in raw_content if isinstance(c, dict)
    )
    if not query.strip():
        raise HTTPException(status_code=400, detail="user message content must not be empty")

    logger.info("AG-UI run — thread_id=%s query=%s", thread_id, query)

    graph = get_main_graph()
    initial_state = {**(data.state or {}), "query": query, "transformation_count": 0}
    config = {"configurable": {"thread_id": thread_id}}

    return StreamingResponse(
        agui_event_stream(graph, initial_state, config),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/collection/create")
async def create_vector_collection(data: Dict):
    collection_name = data.get("collection_name")
    create_status = create_collection(collection_name)
    logger.info("Create collection %s status: %s", collection_name, create_status)
    return {"status": create_status is not None}


def _index_document(doc_id, title, content, collection_name=DEFAULT_COLLECTION_NAME):
    text = title + " " + content
    nodes = split_document(text)
    status_list = []
    for node in nodes:
        vector = get_embedding(node.text)
        status = add_vector(
            collection_name=collection_name,
            vectors={doc_id: {"vector": vector, "payload": {"title": title, "content": node.text}}},
        )
        status_list.append(status)
    logger.info("Add vector status: %s", status_list)
    return status_list


@app.post("/document/create")
async def create_document(data: Dict):
    doc_id = data.get("id")
    title = data.get("title")
    content = data.get("content")
    create_status = insert_document(title, content)
    logger.info("Create document status: %s", create_status)
    index_status = _index_document(doc_id, title, content)
    return {"status": create_status is not None, "index_status": index_status}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8002, workers=2, log_level="info")

