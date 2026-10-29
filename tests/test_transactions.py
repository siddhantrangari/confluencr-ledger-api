import uuid
from decimal import Decimal

import pytest

from tests.helpers import assert_ledger_consistent, balance_of, create_account, post_txn


def test_credit_increases_balance(client):
    acc = create_account(client)
    post_txn(client, acc, "credit", 100, "r1")
    res = post_txn(client, acc, "credit", 50, "r2", "salary")
    assert res.status_code == 201
    body = res.json()
    assert body["account"] == {"account_id": acc, "balance": "150.00"}
    txn = body["transaction"]
    assert txn["type"] == "credit" and txn["amount"] == "50.00"
    assert txn["description"] == "salary" and txn["reference_id"] == "r2"
    assert txn["account_id"] == acc and txn["transfer_id"] is None
    uuid.UUID(txn["id"])
    assert balance_of(client, acc) == Decimal("150")


def test_debit_decreases_balance(client):
    acc = create_account(client)
    post_txn(client, acc, "credit", 100, "r1")
    res = post_txn(client, acc, "debit", 30, "r2")
    assert res.status_code == 201
    assert res.json()["account"]["balance"] == "70.00"


def test_debit_with_insufficient_balance_is_rejected(client, db):
    acc = create_account(client)
    post_txn(client, acc, "credit", 100, "r1")
    res = post_txn(client, acc, "debit", 150, "r2")
    assert res.status_code == 422
    error = res.json()["error"]
    assert error["code"] == "INSUFFICIENT_BALANCE"
    assert error["details"]["balance"] == "100.00"
    assert error["details"]["requested_amount"] == "150.00"
    assert balance_of(client, acc) == Decimal("100")
    # The rejected debit left no row behind.
    assert db.execute("SELECT count(*) AS n FROM transactions WHERE account_id = %s", (acc,)).fetchone()["n"] == 1


def test_debit_of_exact_balance_is_allowed(client):
    acc = create_account(client)
    post_txn(client, acc, "credit", 100, "r1")
    assert post_txn(client, acc, "debit", 100, "r2").status_code == 201
    assert balance_of(client, acc) == 0


def test_debit_on_new_account_is_rejected(client):
    acc = create_account(client)
    assert post_txn(client, acc, "debit", "0.01", "r1").status_code == 422


def test_balance_equals_sum_of_valid_transactions(client, db):
    """The brief's example: 0 +100 +50 -30 -20 = 100, plus a rejected debit that must not count."""
    acc = create_account(client)
    for type_, amount, ref in [("credit", 100, "a"), ("credit", 50, "b"), ("debit", 30, "c"), ("debit", 20, "d")]:
        assert post_txn(client, acc, type_, amount, ref).status_code == 201
    assert post_txn(client, acc, "debit", 1000, "e").status_code == 422
    assert balance_of(client, acc) == Decimal("100")
    assert assert_ledger_consistent(db, acc) == Decimal("100")


def test_duplicate_reference_creates_nothing_and_returns_existing(client, db):
    acc = create_account(client)
    first = post_txn(client, acc, "credit", 100, "dup-1").json()["transaction"]

    res = post_txn(client, acc, "credit", 100, "dup-1")
    assert res.status_code == 409
    error = res.json()["error"]
    assert error["code"] == "DUPLICATE_TRANSACTION"
    assert error["details"]["existing_transaction"] == first
    assert balance_of(client, acc) == Decimal("100")
    assert db.execute("SELECT count(*) AS n FROM transactions").fetchone()["n"] == 1


def test_duplicate_with_different_payload_is_still_rejected(client):
    acc = create_account(client)
    post_txn(client, acc, "credit", 100, "dup-1")
    res = post_txn(client, acc, "debit", 5, "dup-1")
    assert res.status_code == 409
    assert res.json()["error"]["details"]["existing_transaction"]["amount"] == "100.00"
    assert balance_of(client, acc) == Decimal("100")


def test_duplicate_is_reported_before_insufficient_balance(client):
    """A retried debit that already succeeded must say 'already exists', not 'insufficient'."""
    acc = create_account(client)
    post_txn(client, acc, "credit", 100, "r1")
    post_txn(client, acc, "debit", 80, "pay-1")  # balance now 20
    res = post_txn(client, acc, "debit", 80, "pay-1")  # retry of the same payment
    assert res.status_code == 409
    assert balance_of(client, acc) == Decimal("20")


def test_same_reference_allowed_on_different_accounts(client):
    a, b = create_account(client, "A"), create_account(client, "B")
    assert post_txn(client, a, "credit", 10, "shared-ref").status_code == 201
    assert post_txn(client, b, "credit", 20, "shared-ref").status_code == 201
    assert balance_of(client, a) == 10 and balance_of(client, b) == 20


def test_rejected_reference_can_be_reused(client):
    """A rejected debit stores nothing, so the client may retry the same reference later."""
    acc = create_account(client)
    assert post_txn(client, acc, "debit", 50, "pay-1").status_code == 422
    post_txn(client, acc, "credit", 100, "topup")
    assert post_txn(client, acc, "debit", 50, "pay-1").status_code == 201


def test_reference_id_is_case_sensitive(client):
    acc = create_account(client)
    assert post_txn(client, acc, "credit", 1, "ABC").status_code == 201
    assert post_txn(client, acc, "credit", 1, "abc").status_code == 201


def test_transaction_on_unknown_account_is_404(client):
    res = post_txn(client, str(uuid.uuid4()), "credit", 10, "r1")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_transaction_on_malformed_account_id_is_400(client):
    res = post_txn(client, "abc", "credit", 10, "r1")
    assert res.status_code == 400


@pytest.mark.parametrize("bad_type", ["refund", "CREDIT", "", None, 1])
def test_invalid_type(client, bad_type):
    acc = create_account(client)
    res = post_txn(client, acc, bad_type, 10, "r1")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_TRANSACTION_TYPE"


@pytest.mark.parametrize("bad_amount", [0, -5, "0.00", "-1", "10.001", 0.001, "abc", None, True, "NaN", "Infinity", 1_000_000_000.01])
def test_invalid_amount(client, bad_amount):
    acc = create_account(client)
    res = post_txn(client, acc, "credit", bad_amount, "r1")
    assert res.status_code == 400, bad_amount
    assert res.json()["error"]["code"] == "INVALID_AMOUNT"
    assert balance_of(client, acc) == 0


@pytest.mark.parametrize("amount, stored", [(10, "10.00"), ("10.5", "10.50"), (0.1, "0.10"), ("1000000000.00", "1000000000.00")])
def test_valid_amount_formats(client, amount, stored):
    acc = create_account(client)
    res = post_txn(client, acc, "credit", amount, "r1")
    assert res.status_code == 201
    assert res.json()["transaction"]["amount"] == stored


def test_decimal_amounts_have_no_float_drift(client):
    """0.1 + 0.2 is 0.30000000000000004 in floating point; here it must be exactly 0.30."""
    acc = create_account(client)
    post_txn(client, acc, "credit", 0.1, "a")
    post_txn(client, acc, "credit", 0.2, "b")
    assert client.get(f"/accounts/{acc}/balance").json()["balance"] == "0.30"


@pytest.mark.parametrize("bad_ref", ["", "   ", None, "x" * 65, 123])
def test_invalid_reference_id(client, bad_ref):
    acc = create_account(client)
    res = post_txn(client, acc, "credit", 10, bad_ref)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_missing_reference_id(client):
    acc = create_account(client)
    res = client.post(f"/accounts/{acc}/transactions", json={"type": "credit", "amount": 10})
    assert res.status_code == 400
    assert res.json()["error"]["details"] == [{"field": "reference_id", "message": "Field required"}]


def test_mixed_validation_errors_use_generic_code(client):
    acc = create_account(client)
    res = client.post(f"/accounts/{acc}/transactions", json={"type": "refund", "amount": -1})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
    assert len(res.json()["error"]["details"]) == 3  # type, amount, reference_id


def test_description_is_optional_and_limited(client):
    acc = create_account(client)
    assert post_txn(client, acc, "credit", 1, "a").json()["transaction"]["description"] is None
    assert post_txn(client, acc, "credit", 1, "b", "x" * 256).status_code == 400


def test_balance_limit_is_enforced(client, db):
    acc = create_account(client)
    # Put the account just under the NUMERIC(18,2) ceiling directly, then try to credit past it.
    db.execute("UPDATE accounts SET balance = 9999999999999999.00 WHERE id = %s", (acc,))
    res = post_txn(client, acc, "credit", 1, "r1")
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "BALANCE_LIMIT_EXCEEDED"
    assert db.execute("SELECT count(*) AS n FROM transactions").fetchone()["n"] == 0
