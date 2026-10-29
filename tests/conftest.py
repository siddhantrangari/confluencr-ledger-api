"""Tests run against a REAL Postgres (the ledger_test database), never mocks:
locking, constraints and triggers are exactly what we need to prove, and only Postgres can.
"""

import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.db import create_pool
from app.main import create_app
from app.migrate import migrate

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://ledger:ledger@localhost:5433/ledger_test"
)


@pytest.fixture(scope="session", autouse=True)
def migrated():
    migrate(TEST_DATABASE_URL)


@pytest.fixture(autouse=True)
def clean_db():
    # TRUNCATE is not a row-level UPDATE/DELETE, so the immutability trigger does not fire.
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("TRUNCATE transactions, accounts")
    yield


@pytest.fixture
def client():
    # raise_server_exceptions=False so a 500 comes back as a response we can assert on.
    with TestClient(create_app(TEST_DATABASE_URL), raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def pool():
    """Direct pool for concurrency tests: each thread gets its own real DB connection."""
    p = create_pool(TEST_DATABASE_URL, max_size=25)  # >= workers, so every thread holds a connection
    p.open(wait=True)
    yield p
    p.close()


@pytest.fixture
def db():
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=dict_row) as conn:
        yield conn
