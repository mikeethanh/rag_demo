from typing import TypedDict


class GraphState(TypedDict, total=False):
    query: str
    documents: list          # raw Qdrant payloads {title, content}
    generation: str
    transformation_count: int
    follow_up_questions: list
    source_documents: list   # {title, source, page} for citations
    _grade_avg: float        # internal: avg relevance score from grade_docs node
    branch: str              # "legal" | "general" | "calculation" | "web_search"
