"""Minimal migration runner: applies migrations/*.sql in filename order, once each.

Usage: python -m app.migrate            (uses DATABASE_URL)
"""

from pathlib import Path

import psycopg

from app.config import database_url

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def migrate(url: str) -> list[str]:
    applied_now = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   filename   TEXT PRIMARY KEY,
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )"""
        )
        done = {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            # Each migration and its bookkeeping row commit together, or not at all.
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            applied_now.append(path.name)
    return applied_now


if __name__ == "__main__":
    applied = migrate(database_url())
    print(f"Applied: {', '.join(applied)}" if applied else "Database is up to date.")
