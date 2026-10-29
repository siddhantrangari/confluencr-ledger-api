"""Domain errors. Each carries its HTTP status and a stable machine-readable `code`.

Every error response has the same shape:
    {"error": {"code": "...", "message": "...", "details": ..., "request_id": "..."}}
"""

from typing import Any

from app.schemas import Transaction


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.message = message
        self.details = details


class AccountNotFound(AppError):
    status_code = 404
    code = "ACCOUNT_NOT_FOUND"

    def __init__(self, account_id):
        super().__init__(f"Account {account_id} does not exist", {"account_id": str(account_id)})


class DuplicateTransaction(AppError):
    status_code = 409
    code = "DUPLICATE_TRANSACTION"

    def __init__(self, existing: dict):
        super().__init__(
            f"A transaction with reference_id '{existing['reference_id']}' already exists "
            f"for account {existing['account_id']}; no new transaction was created",
            {"existing_transaction": Transaction.model_validate(existing).model_dump(mode="json")},
        )


class InsufficientBalance(AppError):
    status_code = 422
    code = "INSUFFICIENT_BALANCE"

    def __init__(self, account_id, balance, amount):
        super().__init__(
            f"Insufficient balance: available {balance}, requested {amount}",
            {"account_id": str(account_id), "balance": str(balance), "requested_amount": str(amount)},
        )


class SameAccountTransfer(AppError):
    status_code = 400
    code = "SAME_ACCOUNT_TRANSFER"

    def __init__(self):
        super().__init__("from_account_id and to_account_id must be different accounts")


class BalanceLimitExceeded(AppError):
    status_code = 422
    code = "BALANCE_LIMIT_EXCEEDED"

    def __init__(self):
        super().__init__("This transaction would exceed the maximum balance an account can hold")
