import logging
import os

from brain import openai_chat_complete, get_embedding
from vectorize import search_vector
from configs import DEFAULT_COLLECTION_NAME
from agent.state import GraphState

logger = logging.getLogger(__name__)

_REWRITE_SYSTEM = (
    "You are an expert legal query optimizer. "
    "Think step-by-step about the legal intent of the query, "
    "then rewrite it to maximize semantic search recall over legal document embeddings. "
    "Return ONLY the rewritten query — no explanation."
)

_GRADE_SYSTEM = (
    "You are a legal document relevance grader. "
    "Given a query and a document chunk, score how relevant the document is "
    "to answering the query. Respond with a single float between 0 and 1. "
    "0 = completely irrelevant, 1 = directly answers the query. "
    "Return ONLY the number."
)

_GENERATE_SYSTEM = (
    "You are a precise legal assistant. "
    "Reason through the provided statutes and document chunks step-by-step. "
    "Cite document indices (e.g. [Doc 1], [Doc 2]) inline when you use them. "
    "Then write a clear, concise answer grounded strictly in the provided documents."
)

_FALLBACK_SYSTEM = (
    "You are a legal assistant. "
    "The retrieval system could not find sufficiently relevant documents for this query. "
    "Answer based on your general legal knowledge. "
    "Make clear that this answer is not sourced from specific retrieved documents."
)

_FOLLOW_UP_SYSTEM = (
    "You are a legal research assistant. "
    "Based on the query, the retrieved documents, and the answer provided, "
    "generate exactly 3 short follow-up questions the user might want to ask next. "
    "Return them as a numbered list: 1. ... 2. ... 3. ..."
)


def rewrite(state: GraphState) -> GraphState:
    query = state["query"]
    count = state.get("transformation_count", 0)
    logger.info("Node rewrite — attempt %d, query: %s", count + 1, query)
    messages = [
        {"role": "system", "content": _REWRITE_SYSTEM},
        {"role": "user", "content": query},
    ]
    rewritten = openai_chat_complete(messages)
    logger.info("Rewritten query: %s", rewritten)
    return {
        **state,
        "query": rewritten,
        "transformation_count": count + 1,
    }


def retrieve(state: GraphState) -> GraphState:
    query = state["query"]
    logger.info("Node retrieve — query: %s", query)
    vector = get_embedding(query)
    payloads = search_vector(DEFAULT_COLLECTION_NAME, vector, limit=6)
    source_documents = [
        {
            "title": p.get("title", ""),
            "source": p.get("source", ""),
            "page": p.get("page", ""),
            "content": p.get("content", ""),
        }
        for p in payloads
    ]
    logger.info("Retrieved %d documents", len(payloads))
    return {
        **state,
        "documents": payloads,
        "source_documents": source_documents,
    }


def _score_document(query: str, doc: dict) -> float:
    content = doc.get("content", "")
    title = doc.get("title", "")
    messages = [
        {"role": "system", "content": _GRADE_SYSTEM},
        {"role": "user", "content": f"Query: {query}\n\nDocument title: {title}\nDocument content: {content}"},
    ]
    try:
        raw = openai_chat_complete(messages).strip()
        return float(raw)
    except (ValueError, AttributeError):
        return 0.0


def grade_docs(state: GraphState) -> GraphState:
    query = state["query"]
    documents = state.get("documents", [])
    logger.info("Node grade_docs — grading %d documents", len(documents))
    if not documents:
        return {**state, "_grade_avg": 0.0}
    scores = [_score_document(query, doc) for doc in documents]
    avg = sum(scores) / len(scores)
    logger.info("Grade avg_score: %.3f", avg)
    return {**state, "_grade_avg": avg}


def generate(state: GraphState) -> GraphState:
    query = state["query"]
    documents = state.get("documents", [])
    logger.info("Node generate — building prompt with %d docs", len(documents))
    doc_context = "\n\n".join(
        f"[Doc {i+1}] Title: {d.get('title','')}\n{d.get('content','')}"
        for i, d in enumerate(documents)
    )
    messages = [
        {"role": "system", "content": _GENERATE_SYSTEM},
        {"role": "user", "content": f"Documents:\n{doc_context}\n\nQuery: {query}"},
    ]
    answer = openai_chat_complete(messages)
    logger.info("Generation complete")
    return {**state, "generation": answer}


def fallback(state: GraphState) -> GraphState:
    query = state["query"]
    logger.info("Node fallback — no citations, query: %s", query)
    messages = [
        {"role": "system", "content": _FALLBACK_SYSTEM},
        {"role": "user", "content": query},
    ]
    answer = openai_chat_complete(messages)
    return {**state, "generation": answer, "source_documents": [], "follow_up_questions": []}


def follow_up(state: GraphState) -> GraphState:
    query = state["query"]
    generation = state.get("generation", "")
    documents = state.get("documents", [])
    doc_summary = "\n".join(d.get("title", "") for d in documents[:3])
    logger.info("Node follow_up")
    messages = [
        {"role": "system", "content": _FOLLOW_UP_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Original query: {query}\n"
                f"Relevant document titles:\n{doc_summary}\n"
                f"Answer given:\n{generation}"
            ),
        },
    ]
    raw = openai_chat_complete(messages)
    questions = _parse_follow_up(raw)
    return {**state, "follow_up_questions": questions}


def _parse_follow_up(text: str) -> list:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    questions = []
    for line in lines:
        # strip leading "1. ", "2. ", "- " etc.
        for prefix in ("1.", "2.", "3.", "-", "*"):
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
                break
        if line:
            questions.append(line)
    return questions[:3]


def rollback_router(state: GraphState) -> str:
    avg = state.get("_grade_avg", 0.0)
    count = state.get("transformation_count", 0)
    if avg >= 0.7:
        return "generate"
    if count < 3:
        return "rewrite"
    return "fallback"
