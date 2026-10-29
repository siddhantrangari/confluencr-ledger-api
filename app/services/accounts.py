from uuid import UUID

from psycopg import Connection

from app.errors import AccountNotFound

ACCOUNT_COLUMNS = "id, name, balance, created_at"


def create_account(conn: Connection, name: str) -> dict:
    # id, balance (0) and created_at all come from column defaults; the client only supplies name.
    return conn.execute(
        f"INSERT INTO accounts (name) VALUES (%s) RETURNING {ACCOUNT_COLUMNS}", (name,)
    ).fetchone()


def get_account(conn: Connection, account_id: UUID) -> dict:
    row = conn.execute(
        f"SELECT {ACCOUNT_COLUMNS} FROM accounts WHERE id = %s", (account_id,)
    ).fetchone()
    if row is None:
        raise AccountNotFound(account_id)
    return row


def lock_account(conn: Connection, account_id: UUID) -> dict:
    """SELECT ... FOR UPDATE: take a row lock on the account for the rest of the DB transaction.

    Any other transaction that wants to change this account's balance blocks here until we
    COMMIT or ROLLBACK, so balance checks and updates on one account happen one at a time.
    Must be called inside `with conn.transaction():`.
    """
    row = conn.execute(
        f"SELECT {ACCOUNT_COLUMNS} FROM accounts WHERE id = %s FOR UPDATE", (account_id,)
    ).fetchone()
    if row is None:
        raise AccountNotFound(account_id)
    return row
