import uuid

import pytest

from tests.helpers import create_account, post_txn


@pytest.fixture
def account_with_history(client):
    """7 transactions in order: c1 d1 c2 c3 d2 c4 c5 (4 debits would overdraw, so 2 debits)."""
    acc = create_account(client)
    for i, (type_, ref) in enumerate([("credit", "c1"), ("debit", "d1"), ("credit", "c2"), ("credit", "c3"),
                                       ("debit", "d2"), ("credit", "c4"), ("credit", "c5")]):
        amount = 100 if type_ == "credit" else 10
        assert post_txn(client, acc, type_, amount, ref).status_code == 201
    return acc


def refs(res):
    return [t["reference_id"] for t in res.json()["data"]]


def test_latest_first_with_default_pagination(client, account_with_history):
    res = client.get(f"/accounts/{account_with_history}/transactions")
    assert res.status_code == 200
    assert refs(res) == ["c5", "c4", "d2", "c3", "c2", "d1", "c1"]
    assert res.json()["pagination"] == {"page": 1, "limit": 20, "total_records": 7, "total_pages": 1}


def test_created_at_is_non_increasing(client, account_with_history):
    times = [t["created_at"] for t in client.get(f"/accounts/{account_with_history}/transactions").json()["data"]]
    assert times == sorted(times, reverse=True)


def test_pagination_pages_through_everything_once(client, account_with_history):
    url = f"/accounts/{account_with_history}/transactions"
    p1, p2, p3 = (client.get(url, params={"page": p, "limit": 3}) for p in (1, 2, 3))
    assert refs(p1) == ["c5", "c4", "d2"]
    assert refs(p2) == ["c3", "c2", "d1"]
    assert refs(p3) == ["c1"]
    assert p3.json()["pagination"] == {"page": 3, "limit": 3, "total_records": 7, "total_pages": 3}


def test_page_past_the_end_is_empty_not_an_error(client, account_with_history):
    res = client.get(f"/accounts/{account_with_history}/transactions", params={"page": 99, "limit": 3})
    assert res.status_code == 200
    assert res.json()["data"] == []
    assert res.json()["pagination"]["total_pages"] == 3


def test_filter_by_type(client, account_with_history):
    url = f"/accounts/{account_with_history}/transactions"
    debits = client.get(url, params={"type": "debit"})
    assert refs(debits) == ["d2", "d1"]
    assert debits.json()["pagination"]["total_records"] == 2
    credits = client.get(url, params={"type": "credit", "limit": 2})
    assert refs(credits) == ["c5", "c4"]
    assert credits.json()["pagination"] == {"page": 1, "limit": 2, "total_records": 5, "total_pages": 3}


def test_empty_history(client):
    acc = create_account(client)
    res = client.get(f"/accounts/{acc}/transactions")
    assert res.json() == {"data": [], "pagination": {"page": 1, "limit": 20, "total_records": 0, "total_pages": 0}}


def test_history_only_shows_own_account(client, account_with_history):
    other = create_account(client, "Other")
    post_txn(client, other, "credit", 1, "c1")
    assert client.get(f"/accounts/{other}/transactions").json()["pagination"]["total_records"] == 1


@pytest.mark.parametrize("params", [
    {"page": 0}, {"page": -1}, {"page": "abc"}, {"page": 1.5}, {"page": 1_000_001},
    {"limit": 0}, {"limit": 101}, {"limit": "ten"},
])
def test_invalid_pagination(client, account_with_history, params):
    res = client.get(f"/accounts/{account_with_history}/transactions", params=params)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_PAGINATION"


def test_invalid_type_filter(client, account_with_history):
    res = client.get(f"/accounts/{account_with_history}/transactions", params={"type": "refund"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_TRANSACTION_TYPE"


def test_history_of_unknown_account_is_404(client):
    res = client.get(f"/accounts/{uuid.uuid4()}/transactions")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"
