import logging
import os
from openai import OpenAI

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", default=None)

_client = None


def get_openai_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def openai_chat_complete(messages=(), model="gpt-4o-mini", raw=False, tools=None):
    client = get_openai_client()
    kwargs = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    response = client.chat.completions.create(**kwargs)
    if raw:
        return response.choices[0].message
    return response.choices[0].message.content


def get_embedding(text, model="text-embedding-3-large"):
    client = get_openai_client()
    text = text.replace("\n", " ")
    return client.embeddings.create(input=[text], model=model).data[0].embedding
