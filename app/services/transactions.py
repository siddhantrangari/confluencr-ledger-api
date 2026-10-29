from decimal import Decimal
from math import ceil
from uuid import UUID

from psycopg import Connection, errors as pg_errors

from app.errors import BalanceLimitExceeded, DuplicateTransaction, InsufficientBalance
from app.schemas import CreateTransactionRequest
from app.services.accounts import get_account, lock_account

TXN_COLUMNS = "id, account_id, type, amount, description, reference_id, transfer_id, created_at"


def find_by_reference(conn: Connection, account_id: UUID, reference_id: str) -> dict | None:
    return conn.execute(
        f"SELECT {TXN_COLUMNS} FROM transactions WHERE account_id = %s AND reference_id = %s",
        (account_id, reference_id),
    ).fetchone()


def insert_transaction(
    conn: Connection,
    account_id: UUID,
    type_: str,
    amount: Decimal,
    reference_id: str,
    description: str | None,
    transfer_id: UUID | None = None,
) -> dict:
    return conn.execute(
        f"""INSERT INTO transactions (account_id, type, amount, reference_id, description, transfer_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING {TXN_COLUMNS}""",
        (account_id, type_, amount, reference_id, description, transfer_id),
    ).fetchone()


def apply_balance_change(conn: Connection, account_id: UUID, type_: str, amount: Decimal) -> Decimal:
    # Relative update (balance = balance + delta) computed by Postgres on the locked row,
    # never "read balance into Python, add, write back", which could lose concurrent updates.
    delta = amount if type_ == "credit" else -amount
    try:
        row = conn.execute(
            "UPDATE accounts SET balance = balance + %s WHERE id = %s RETURNING balance",
            (delta, account_id),
        ).fetchone()
    except pg_errors.NumericValueOutOfRange:
        raise BalanceLimitExceeded() from None
    return row["balance"]


def create_transaction(conn: Connection, account_id: UUID, req: CreateTransactionRequest) -> tuple[dict, Decimal]:
    """Insert one credit/debit and update the balance as a single atomic DB transaction.

    Order of steps matters:
      1. Lock the account row: concurrent requests for this account now queue up behind us.
      2. Duplicate check BEFORE the balance check: retrying a debit that already succeeded must
         answer "already exists", even if the balance has since dropped below the amount.
      3. Balance check for debits (the row is locked, so the balance can't change under us).
      4. Insert the transaction, then update the balance.
    Any exception inside the `with` block rolls everything back, so nothing is half-written.
    """
    with conn.transaction():
        account = lock_account(conn, account_id)

        existing = find_by_reference(conn, account_id, req.reference_id)
        if existing is not None:
            raise DuplicateTransaction(existing)

        if req.type == "debit" and account["balance"] < req.amount:
            raise InsufficientBalance(account_id, account["balance"], req.amount)

        txn = insert_transaction(conn, account_id, req.type, req.amount, req.reference_id, req.description)
        balance = apply_balance_change(conn, account_id, req.type, req.amount)
    return txn, balance


def list_transactions(
    conn: Connection, account_id: UUID, type_: str | None, page: int, limit: int
) -> tuple[list[dict], dict]:
    filters, params = ["account_id = %s"], [account_id]
    if type_ is not None:
        filters.append("type = %s")
        params.append(type_)
    where = " AND ".join(filters)

    # REPEATABLE READ: the existence check, the COUNT and the page query all see the same
    # snapshot, so total_records can't disagree with the rows returned if a write lands mid-request.
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        get_account(conn, account_id)
        total = conn.execute(f"SELECT count(*) AS n FROM transactions WHERE {where}", params).fetchone()["n"]
        rows = conn.execute(
            f"""SELECT {TXN_COLUMNS} FROM transactions WHERE {where}
                ORDER BY created_at DESC, seq DESC
                LIMIT %s OFFSET %s""",
            [*params, limit, (page - 1) * limit],
        ).fetchall()

    pagination = {
        "page": page,
        "limit": limit,
        "total_records": total,
        "total_pages": ceil(total / limit),
    }
    return rows, pagination
