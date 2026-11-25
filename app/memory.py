"""Conversation memory, stored in the chat_messages table.

A conversation is a UUID. Every question and answer is saved under it, so a follow-up
like "and per month?" can be answered with the earlier turns as context.

    history()        last few messages, oldest first -> passed to graph.chat(history=...)
    save_turn()      store one question + its answer
    list_messages()  full thread (up to 100) for showing in the UI

Every query filters on tenant_id AND conversation_id, so one company can never read
another company's conversation even if it guesses the ID.
"""
import uuid

from app.config import settings
from app.db import connection


def history(tenant: str, conversation_id: uuid.UUID):
    """The most recent `memory_turns` question/answer pairs (2 rows per turn), oldest first."""
    with connection() as conn:
        # Take the newest N rows (ORDER BY id DESC LIMIT N), then flip them back to oldest-first.
        rows = conn.execute("""SELECT role,content FROM (
            SELECT id,role,content,created_at FROM chat_messages
            WHERE tenant_id=%s AND conversation_id=%s ORDER BY id DESC LIMIT %s
        ) recent ORDER BY id ASC""", (tenant, conversation_id, settings().memory_turns * 2)).fetchall()
    return rows


def save_turn(tenant: str, conversation_id: uuid.UUID, question: str, response: str):
    """Store the question and its answer together, in one transaction."""
    with connection() as conn:
        conn.execute("""INSERT INTO chat_messages(tenant_id,conversation_id,role,content)
                        VALUES (%s,%s,'user',%s),(%s,%s,'assistant',%s)""",
                     (tenant, conversation_id, question, tenant, conversation_id, response))


def list_messages(tenant: str, conversation_id: uuid.UUID):
    """The whole thread with timestamps, oldest first (capped at 100), for display."""
    with connection() as conn:
        return conn.execute("""SELECT role,content,created_at FROM chat_messages
          WHERE tenant_id=%s AND conversation_id=%s ORDER BY id ASC LIMIT 100""",
                            (tenant, conversation_id)).fetchall()
