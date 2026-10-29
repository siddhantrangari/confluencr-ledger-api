import uuid
from decimal import Decimal

import pytest

from app.services import transfers as transfer_service
from tests.helpers import assert_ledger_consistent, balance_of, create_account, post_txn


@pytest.fixture
def a_and_b(client):
    """The brief's example: A has 100, B has 50."""
    a, b = create_account(client, "A"), create_account(client, "B")
    post_txn(client, a, "credit", 100, "seed")
    post_txn(client, b, "credit", 50, "seed")
    return a, b


def transfer(client, src, dst, amount, ref="t1", **extra):
    return client.post("/transfers", json={
        "from_account_id": src, "to_account_id": dst, "amount": amount, "reference_id": ref, **extra,
    })


def test_transfer_moves_money(client, db, a_and_b):
    a, b = a_and_b
    res = transfer(client, a, b, 30)
    assert res.status_code == 201
    body = res.json()
    assert body["from_account"] == {"account_id": a, "balance": "70.00"}
    assert body["to_account"] == {"account_id": b, "balance": "80.00"}
    assert body["debit"]["type"] == "debit" and body["debit"]["account_id"] == a
    assert body["credit"]["type"] == "credit" and body["credit"]["account_id"] == b
    # Both legs are linked by the same transfer_id and carry the client's reference_id.
    assert body["debit"]["transfer_id"] == body["credit"]["transfer_id"] == body["transfer_id"]
    assert body["debit"]["reference_id"] == body["credit"]["reference_id"] == "t1"
    assert body["debit"]["description"] == f"Transfer to {b}"
    assert balance_of(client, a) == 70 and balance_of(client, b) == 80
    assert_ledger_consistent(db, a)
    assert_ledger_consistent(db, b)


def test_transfer_legs_appear_in_history(client, a_and_b):
    a, b = a_and_b
    transfer(client, a, b, 30, description="rent share")
    latest_a = client.get(f"/accounts/{a}/transactions").json()["data"][0]
    latest_b = client.get(f"/accounts/{b}/transactions").json()["data"][0]
    assert (latest_a["type"], latest_a["description"]) == ("debit", "rent share")
    assert (latest_b["type"], latest_b["description"]) == ("credit", "rent share")


def test_transfer_with_insufficient_balance_changes_nothing(client, db, a_and_b):
    a, b = a_and_b
    res = transfer(client, a, b, 150)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INSUFFICIENT_BALANCE"
    assert balance_of(client, a) == 100 and balance_of(client, b) == 50
    assert db.execute("SELECT count(*) AS n FROM transactions WHERE transfer_id IS NOT NULL").fetchone()["n"] == 0


@pytest.mark.parametrize("missing_side", ["from", "to"])
def test_transfer_requires_both_accounts_to_exist(client, a_and_b, missing_side):
    a, _ = a_and_b
    ghost = str(uuid.uuid4())
    src, dst = (ghost, a) if missing_side == "from" else (a, ghost)
    res = transfer(client, src, dst, 10)
    assert res.status_code == 404
    assert res.json()["error"]["details"] == {"account_id": ghost}
    assert balance_of(client, a) == 100


def test_self_transfer_is_rejected(client, a_and_b):
    a, _ = a_and_b
    res = transfer(client, a, a, 10)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "SAME_ACCOUNT_TRANSFER"


def test_duplicate_transfer_is_rejected(client, a_and_b):
    a, b = a_and_b
    assert transfer(client, a, b, 30, ref="t-dup").status_code == 201
    res = transfer(client, a, b, 30, ref="t-dup")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "DUPLICATE_TRANSACTION"
    assert balance_of(client, a) == 70 and balance_of(client, b) == 80


def test_transfer_reference_already_used_on_destination(client, a_and_b):
    a, b = a_and_b
    post_txn(client, b, "credit", 1, "taken")
    res = transfer(client, a, b, 30, ref="taken")
    assert res.status_code == 409
    assert balance_of(client, a) == 100 and balance_of(client, b) == 51


@pytest.mark.parametrize("body_patch", [{"amount": 0}, {"amount": "1.234"}, {"from_account_id": "nope"}, {"reference_id": ""}])
def test_transfer_validation(client, a_and_b, body_patch):
    a, b = a_and_b
    body = {"from_account_id": a, "to_account_id": b, "amount": 10, "reference_id": "t1", **body_patch}
    assert client.post("/transfers", json=body).status_code == 400


def test_failure_between_legs_rolls_back_whole_transfer(client, db, a_and_b, monkeypatch):
    """Debit leg is written, then the credit side blows up: nothing may survive."""
    a, b = a_and_b
    real = transfer_service.apply_balance_change
    calls = []

    def fail_on_second_call(conn, account_id, type_, amount):
        calls.append(type_)
        if len(calls) == 2:  # 1st call = debit of A succeeded; 2nd = credit of B fails
            raise RuntimeError("simulated crash between transfer legs")
        return real(conn, account_id, type_, amount)

    monkeypatch.setattr(transfer_service, "apply_balance_change", fail_on_second_call)
    res = transfer(client, a, b, 30)
    assert res.status_code == 500
    assert calls == ["debit", "credit"]

    monkeypatch.undo()
    assert balance_of(client, a) == Decimal("100") and balance_of(client, b) == Decimal("50")
    assert db.execute("SELECT count(*) AS n FROM transactions WHERE transfer_id IS NOT NULL").fetchone()["n"] == 0
    assert_ledger_consistent(db, a)
    assert_ledger_consistent(db, b)
