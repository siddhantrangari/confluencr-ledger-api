"""Request and response models (Pydantic). These also generate the OpenAPI spec at /docs.

Money is `Decimal` end to end: parsed from JSON as Decimal, stored as NUMERIC(18,2),
returned as a string like "150.00". A float never touches an amount.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

MAX_AMOUNT = Decimal("1000000000.00")

# > 0, at most 2 decimal places, at most 1,000,000,000.00, no NaN/Infinity.
# After validation the value is normalised to exactly 2 places (10 -> 10.00) so it prints consistently.
Amount = Annotated[
    Decimal,
    Field(gt=0, le=MAX_AMOUNT, decimal_places=2, allow_inf_nan=False, examples=["50.00"]),
    AfterValidator(lambda d: d.quantize(Decimal("0.01"))),
]
AccountName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
# Exact match, case-sensitive, not trimmed (it is an idempotency key) but must not be blank.
ReferenceId = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"\S")]
Description = Annotated[str, StringConstraints(max_length=255)]
TransactionType = Literal["credit", "debit"]


class StrictModel(BaseModel):
    # Unknown fields are rejected, so e.g. {"name": "A", "balance": 500} is a 400, not silently ignored.
    model_config = ConfigDict(extra="forbid")


# ---------- requests ----------

class CreateAccountRequest(StrictModel):
    name: AccountName


class CreateTransactionRequest(StrictModel):
    type: TransactionType
    amount: Amount
    reference_id: ReferenceId
    description: Description | None = None


class CreateTransferRequest(StrictModel):
    from_account_id: UUID
    to_account_id: UUID
    amount: Amount
    reference_id: ReferenceId
    description: Description | None = None


# ---------- responses ----------

class Account(BaseModel):
    id: UUID
    name: str
    balance: Decimal
    created_at: datetime


class Balance(BaseModel):
    account_id: UUID
    balance: Decimal


class Transaction(BaseModel):
    id: UUID
    account_id: UUID
    type: TransactionType
    amount: Decimal
    description: str | None
    reference_id: str
    transfer_id: UUID | None
    created_at: datetime


class TransactionCreated(BaseModel):
    transaction: Transaction
    account: Balance


class Pagination(BaseModel):
    page: int
    limit: int
    total_records: int
    total_pages: int


class TransactionPage(BaseModel):
    data: list[Transaction]
    pagination: Pagination


class TransferCreated(BaseModel):
    transfer_id: UUID
    reference_id: str
    amount: Decimal
    debit: Transaction
    credit: Transaction
    from_account: Balance
    to_account: Balance


class ErrorBody(BaseModel):
    code: str
    message: str
    details: object | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
