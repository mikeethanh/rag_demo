import os
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver

_DB_PATH = os.environ.get("CHECKPOINTER_DB_PATH", "/tmp/langgraph_checkpoints.db")


def get_checkpointer():
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    return SqliteSaver(conn)
