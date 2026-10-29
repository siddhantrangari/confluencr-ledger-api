"""Small helpers shared by the test modules."""

from decimal import Decimal


def create_account(client, name="Alice") -> str:
    res = client.post("/accounts", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def post_txn(client, account_id, type_, amount, ref, description=None):
    body = {"type": type_, "amount": amount, "reference_id": ref}
    if description is not None:
        body["description"] = description
    return client.post(f"/accounts/{account_id}/transactions", json=body)


def balance_of(client, account_id) -> Decimal:
    return Decimal(client.get(f"/accounts/{account_id}/balance").json()["balance"])


def assert_ledger_consistent(db, account_id):
    """The core invariant: stored balance == sum(credits) - sum(debits)."""
    row = db.execute(
        """SELECT a.balance,
                  COALESCE(SUM(CASE t.type WHEN 'credit' THEN t.amount ELSE -t.amount END), 0) AS computed
           FROM accounts a LEFT JOIN transactions t ON t.account_id = a.id
           WHERE a.id = %s GROUP BY a.balance""",
        (account_id,),
    ).fetchone()
    assert row["balance"] == row["computed"], row
    assert row["balance"] >= 0
    return row["balance"]
