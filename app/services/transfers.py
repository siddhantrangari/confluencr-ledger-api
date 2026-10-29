from uuid import uuid4

from psycopg import Connection

from app.errors import DuplicateTransaction, InsufficientBalance, SameAccountTransfer
from app.schemas import CreateTransferRequest
from app.services.accounts import lock_account
from app.services.transactions import apply_balance_change, find_by_reference, insert_transaction


def create_transfer(conn: Connection, req: CreateTransferRequest) -> dict:
    """Debit the source and credit the destination in ONE database transaction.

    Either both legs and both balance updates commit, or none of them do.
    """
    if req.from_account_id == req.to_account_id:
        raise SameAccountTransfer()

    with conn.transaction():
        # Deadlock avoidance: always lock the two accounts in the same (sorted) order.
        # If A->B and B->A run concurrently, both lock min(A, B) first, so the second one
        # simply waits for the first instead of each holding one lock and waiting for the other.
        locked = {}
        for account_id in sorted([req.from_account_id, req.to_account_id]):
            locked[account_id] = lock_account(conn, account_id)
        source = locked[req.from_account_id]

        # Both legs store the client's reference_id, so it must be free on both accounts.
        for account_id in (req.from_account_id, req.to_account_id):
            existing = find_by_reference(conn, account_id, req.reference_id)
            if existing is not None:
                raise DuplicateTransaction(existing)

        if source["balance"] < req.amount:
            raise InsufficientBalance(req.from_account_id, source["balance"], req.amount)

        transfer_id = uuid4()
        debit = insert_transaction(
            conn, req.from_account_id, "debit", req.amount, req.reference_id,
            req.description or f"Transfer to {req.to_account_id}", transfer_id,
        )
        from_balance = apply_balance_change(conn, req.from_account_id, "debit", req.amount)
        credit = insert_transaction(
            conn, req.to_account_id, "credit", req.amount, req.reference_id,
            req.description or f"Transfer from {req.from_account_id}", transfer_id,
        )
        to_balance = apply_balance_change(conn, req.to_account_id, "credit", req.amount)

    return {
        "transfer_id": transfer_id,
        "reference_id": req.reference_id,
        "amount": debit["amount"],
        "debit": debit,
        "credit": credit,
        "from_account": {"account_id": req.from_account_id, "balance": from_balance},
        "to_account": {"account_id": req.to_account_id, "balance": to_balance},
    }
