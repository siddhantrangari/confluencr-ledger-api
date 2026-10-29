"""Concurrency: many threads, each with its own DB connection, hitting the same account at once.

We call the service layer directly (not HTTP) because the guarantees come from Postgres row
locks and constraints; this way every request truly runs in parallel on a separate connection.
A threading.Barrier releases all workers at the same instant to maximise contention.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from app.errors import AppError, DuplicateTransaction, InsufficientBalance
from app.schemas import CreateTransactionRequest, CreateTransferRequest
from app.services.transactions import create_transaction
from app.services.transfers import create_transfer
from tests.helpers import assert_ledger_consistent, create_account, post_txn

WORKERS = 20


def run_concurrently(pool, jobs):
    """Run each job(conn) in its own thread/connection, all starting together. Returns results/errors."""
    barrier = threading.Barrier(len(jobs))

    def worker(job):
        with pool.connection() as conn:
            barrier.wait()
            try:
                return job(conn)
            except AppError as exc:
                return exc

    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        return list(executor.map(worker, jobs))


def txn_job(account_id, type_, amount, ref):
    req = CreateTransactionRequest(type=type_, amount=Decimal(amount), reference_id=ref)
    return lambda conn: create_transaction(conn, account_id, req)


def test_parallel_debits_never_overdraw(client, db, pool):
    acc = create_account(client)
    post_txn(client, acc, "credit", 100, "seed")
    # 20 debits of 10 against a balance of 100: exactly 10 may succeed.
    results = run_concurrently(pool, [txn_job(acc, "debit", "10", f"d{i}") for i in range(WORKERS)])
    succeeded = [r for r in results if not isinstance(r, Exception)]
    rejected = [r for r in results if isinstance(r, InsufficientBalance)]
    assert len(succeeded) == 10 and len(rejected) == 10
    assert assert_ledger_consistent(db, acc) == Decimal("0")


def test_parallel_credits_lose_no_updates(client, db, pool):
    acc = create_account(client)
    results = run_concurrently(pool, [txn_job(acc, "credit", "1.25", f"c{i}") for i in range(WORKERS)])
    assert not any(isinstance(r, Exception) for r in results)
    assert assert_ledger_consistent(db, acc) == Decimal("1.25") * WORKERS


def test_parallel_mixed_traffic_keeps_balance_consistent(client, db, pool):
    acc = create_account(client)
    post_txn(client, acc, "credit", 50, "seed")
    jobs = [txn_job(acc, "credit" if i % 2 else "debit", "7", f"m{i}") for i in range(WORKERS)]
    run_concurrently(pool, jobs)
    assert_ledger_consistent(db, acc)


def test_parallel_duplicate_submissions_create_exactly_one_transaction(client, db, pool):
    acc = create_account(client)
    results = run_concurrently(pool, [txn_job(acc, "credit", "10", "same-ref") for _ in range(WORKERS)])
    created = [r for r in results if not isinstance(r, Exception)]
    duplicates = [r for r in results if isinstance(r, DuplicateTransaction)]
    assert len(created) == 1 and len(duplicates) == WORKERS - 1
    assert db.execute("SELECT count(*) AS n FROM transactions").fetchone()["n"] == 1
    assert assert_ledger_consistent(db, acc) == Decimal("10")


def test_opposite_transfers_do_not_deadlock_and_conserve_money(client, db, pool):
    """A->B and B->A at the same time is the classic deadlock; sorted lock order prevents it.
    A deadlock would surface as psycopg.errors.DeadlockDetected (not an AppError) and fail the test."""
    a, b = create_account(client, "A"), create_account(client, "B")
    post_txn(client, a, "credit", 1000, "seed")
    post_txn(client, b, "credit", 1000, "seed")

    def transfer_job(src, dst, ref):
        req = CreateTransferRequest(from_account_id=src, to_account_id=dst, amount=Decimal("5"), reference_id=ref)
        return lambda conn: create_transfer(conn, req)

    jobs = [transfer_job(a, b, f"ab{i}") if i % 2 else transfer_job(b, a, f"ba{i}") for i in range(WORKERS)]
    results = run_concurrently(pool, jobs)
    assert not any(isinstance(r, Exception) for r in results)
    total = assert_ledger_consistent(db, a) + assert_ledger_consistent(db, b)
    assert total == Decimal("2000")  # money is neither created nor destroyed


def test_parallel_transfers_never_overdraw_source(client, db, pool):
    a, b = create_account(client, "A"), create_account(client, "B")
    post_txn(client, a, "credit", 30, "seed")

    def job(i):
        req = CreateTransferRequest(from_account_id=a, to_account_id=b, amount=Decimal("10"), reference_id=f"t{i}")
        return lambda conn: create_transfer(conn, req)

    results = run_concurrently(pool, [job(i) for i in range(WORKERS)])
    assert sum(not isinstance(r, Exception) for r in results) == 3
    assert assert_ledger_consistent(db, a) == 0
    assert assert_ledger_consistent(db, b) == 30
