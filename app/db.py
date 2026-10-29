"""Connection pool and the per-request connection dependency.

Connections run in autocommit mode: a plain SELECT commits immediately, and every
multi-statement write is wrapped explicitly in `with conn.transaction():`, which issues
BEGIN ... COMMIT (or ROLLBACK if an exception escapes the block). That keeps transaction
boundaries visible in the code instead of hidden inside a framework or ORM.
"""

from collections.abc import Iterator

from fastapi import Request
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def create_pool(database_url: str, max_size: int = 10) -> ConnectionPool:
    return ConnectionPool(
        database_url,
        min_size=1,
        max_size=max_size,
        # Session timezone pinned to UTC so every timestamp comes back as UTC.
        kwargs={"autocommit": True, "row_factory": dict_row, "options": "-c timezone=UTC"},
        open=False,
    )


def get_conn(request: Request) -> Iterator[Connection]:
    """FastAPI dependency: borrow one pooled connection for the duration of a request."""
    pool: ConnectionPool = request.app.state.pool
    with pool.connection() as conn:
        yield conn
