import logging
import os

from brain import openai_chat_complete
from agent.state import GraphState

logger = logging.getLogger(__name__)

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

_REWRITE_SYSTEM = (
    "You are a web search query optimizer. "
    "Rewrite the user's query to maximize the quality of web search results. "
    "Make it specific, use keywords, remove conversational filler. "
    "Return ONLY the rewritten query."
)

_SUMMARIZE_SYSTEM = (
    "You are a research summarizer. "
    "Given a user query and a set of web search results, "
    "synthesize the most relevant information into a clear, accurate answer. "
    "Cite sources by their index [1], [2], etc. "
    "Focus on facts directly relevant to the query."
)


def _tavily_search(query: str, max_results: int = 5) -> list:
    """Call Tavily search API. Returns list of {title, url, content}."""
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        response = client.search(query, max_results=max_results)
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
            }
            for r in response.get("results", [])
        ]
    except Exception as exc:
        logger.warning("Tavily search failed: %s", exc)
        return []


def web_search_answer(state: GraphState) -> GraphState:
    query = state["query"]
    logger.info("Branch web_search — query: %s", query)

    # Node 1: rewrite query for web search
    rewrite_messages = [
        {"role": "system", "content": _REWRITE_SYSTEM},
        {"role": "user", "content": query},
    ]
    search_query = openai_chat_complete(rewrite_messages).strip()
    logger.info("Web search rewritten query: %s", search_query)

    # Node 2: Tavily search
    results = _tavily_search(search_query)
    logger.info("Tavily returned %d results", len(results))

    if not results:
        return {
            **state,
            "generation": "No web search results found for this query. Please try rephrasing.",
            "source_documents": [],
            "follow_up_questions": [],
        }

    # Node 3: summarize results
    results_text = "\n\n".join(
        f"[{i+1}] {r['title']}\n{r['content']}" for i, r in enumerate(results)
    )
    summarize_messages = [
        {"role": "system", "content": _SUMMARIZE_SYSTEM},
        {
            "role": "user",
            "content": f"Query: {query}\n\nSearch results:\n{results_text}",
        },
    ]
    answer = openai_chat_complete(summarize_messages)

    source_documents = [
        {"title": r["title"], "source": r["url"], "page": ""}
        for r in results
    ]

    return {
        **state,
        "generation": answer,
        "source_documents": source_documents,
        "follow_up_questions": [],
    }
