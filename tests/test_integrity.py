"""Immutability, atomicity and the database constraints that back them up."""

from decimal import Decimal

import psycopg
import pytest

from app.services import transactions as txn_service
from tests.helpers import assert_ledger_consistent, balance_of, create_account, post_txn


# ---------- immutability ----------

@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_no_api_route_can_modify_transactions(client, method):
    acc = create_account(client)
    txn_id = post_txn(client, acc, "credit", 10, "r1").json()["transaction"]["id"]
    for url in (f"/accounts/{acc}/transactions", f"/accounts/{acc}/transactions/{txn_id}", f"/transactions/{txn_id}"):
        res = client.request(method, url, json={"amount": 999})
        assert res.status_code in (404, 405), (method, url)
    assert balance_of(client, acc) == 10


def test_database_rejects_update_of_a_transaction(client, db):
    acc = create_account(client)
    post_txn(client, acc, "credit", 10, "r1")
    with pytest.raises(psycopg.errors.RestrictViolation, match="immutable"):
        db.execute("UPDATE transactions SET amount = 999")


def test_database_rejects_delete_of_a_transaction(client, db):
    acc = create_account(client)
    post_txn(client, acc, "credit", 10, "r1")
    with pytest.raises(psycopg.errors.RestrictViolation, match="immutable"):
        db.execute("DELETE FROM transactions")
    assert db.execute("SELECT count(*) AS n FROM transactions").fetchone()["n"] == 1


# ---------- atomicity ----------

def test_failure_after_insert_persists_nothing(client, db, monkeypatch):
    """Simulate a crash between 'insert transaction' and 'update balance'."""
    acc = create_account(client)
    post_txn(client, acc, "credit", 100, "r1")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure mid-operation")

    monkeypatch.setattr(txn_service, "apply_balance_change", boom)
    res = post_txn(client, acc, "credit", 50, "r2")

    assert res.status_code == 500
    assert res.json()["error"]["code"] == "INTERNAL_ERROR"
    assert db.execute("SELECT count(*) AS n FROM transactions WHERE reference_id = 'r2'").fetchone()["n"] == 0
    monkeypatch.undo()
    assert balance_of(client, acc) == Decimal("100")
    assert assert_ledger_consistent(db, acc) == Decimal("100")
    # The failed attempt left nothing behind, so the same reference can be retried.
    assert post_txn(client, acc, "credit", 50, "r2").status_code == 201


# ---------- database constraints (defence in depth, tested without the API) ----------

def test_db_rejects_negative_balance(client, db):
    acc = create_account(client)
    with pytest.raises(psycopg.errors.CheckViolation, match="accounts_balance_non_negative"):
        db.execute("UPDATE accounts SET balance = -1 WHERE id = %s", (acc,))


def test_db_rejects_duplicate_reference_per_account(client, db):
    acc = create_account(client)
    insert = "INSERT INTO transactions (account_id, type, amount, reference_id) VALUES (%s, 'credit', 1, 'r')"
    db.execute(insert, (acc,))
    with pytest.raises(psycopg.errors.UniqueViolation, match="transactions_account_reference_unique"):
        db.execute(insert, (acc,))


@pytest.mark.parametrize("type_, amount", [("refund", 1), ("credit", 0), ("debit", -1)])
def test_db_rejects_invalid_type_or_amount(client, db, type_, amount):
    acc = create_account(client)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO transactions (account_id, type, amount, reference_id) VALUES (%s, %s, %s, 'r')",
            (acc, type_, amount),
        )


def test_db_rejects_transaction_for_missing_account(db):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(
            "INSERT INTO transactions (account_id, type, amount, reference_id) "
            "VALUES (gen_random_uuid(), 'credit', 1, 'r')"
        )


def test_account_with_transactions_cannot_be_deleted(client, db):
    acc = create_account(client)
    post_txn(client, acc, "credit", 1, "r")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute("DELETE FROM accounts WHERE id = %s", (acc,))
