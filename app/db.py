"""Postgres access (psycopg 3): pooled `connection()` plus an `audit()` helper."""

import json
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        # Render provides a PostgreSQL connection URL. Prefer its external URL
        # for a FastAPI process running on your computer.
        url = settings().database_url
        if not url:
            raise RuntimeError("DATABASE_URL is missing from .env")
        _pool = ConnectionPool(
            conninfo=url,
            min_size=1,
            max_size=5,
            kwargs={"row_factory": dict_row},
            check=ConnectionPool.check_connection,  # drop connections the server closed
            open=False,
        )
        _pool.open(wait=True, timeout=10)
    return _pool


def close_pool() -> None:
    """Close the pool at app shutdown."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Borrow a connection. Commits on success, rolls back on error.

    Rows come back as dicts:
        with connection() as conn:
            rows = conn.execute("SELECT * FROM documents WHERE tenant_id=%s", (t,)).fetchall()
    """
    with _get_pool().connection() as conn:
        yield conn


def audit(conn: psycopg.Connection, actor: str, tenant: str, action: str, detail: dict) -> None:
    """Record an event in audit_events using the caller's connection/transaction."""
    conn.execute(
        "INSERT INTO audit_events (actor,tenant_id,action,detail) VALUES (%s,%s,%s,%s)",
        (actor, tenant, action, json.dumps(detail)),
    )
