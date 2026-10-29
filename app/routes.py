"""HTTP layer: parse/validate input, call a service, shape the response. No SQL here.

Deliberately absent: any PUT/PATCH/DELETE route for transactions (immutability).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from psycopg import Connection

from app.db import get_conn
from app.schemas import (
    Account,
    Balance,
    CreateAccountRequest,
    CreateTransactionRequest,
    CreateTransferRequest,
    ErrorResponse,
    TransactionCreated,
    TransactionPage,
    TransactionType,
    TransferCreated,
)
from app.services import accounts, transactions, transfers

router = APIRouter()
Conn = Annotated[Connection, Depends(get_conn)]

MAX_PAGE_SIZE = 100
MAX_PAGE = 1_000_000


def errors(*codes: int) -> dict:
    """Document error responses in OpenAPI."""
    return {code: {"model": ErrorResponse} for code in codes}


@router.post(
    "/accounts", status_code=status.HTTP_201_CREATED, response_model=Account,
    tags=["accounts"], responses=errors(400),
)
def create_account(body: CreateAccountRequest, conn: Conn):
    return accounts.create_account(conn, body.name)


@router.get("/accounts/{account_id}", response_model=Account, tags=["accounts"], responses=errors(400, 404))
def get_account(account_id: UUID, conn: Conn):
    return accounts.get_account(conn, account_id)


@router.get("/accounts/{account_id}/balance", response_model=Balance, tags=["accounts"], responses=errors(400, 404))
def get_balance(account_id: UUID, conn: Conn):
    account = accounts.get_account(conn, account_id)
    return {"account_id": account["id"], "balance": account["balance"]}


@router.post(
    "/accounts/{account_id}/transactions", status_code=status.HTTP_201_CREATED,
    response_model=TransactionCreated, tags=["transactions"], responses=errors(400, 404, 409, 422),
)
def create_transaction(account_id: UUID, body: CreateTransactionRequest, conn: Conn):
    txn, balance = transactions.create_transaction(conn, account_id, body)
    return {"transaction": txn, "account": {"account_id": account_id, "balance": balance}}


@router.get(
    "/accounts/{account_id}/transactions", response_model=TransactionPage,
    tags=["transactions"], responses=errors(400, 404),
)
def list_transactions(
    account_id: UUID,
    conn: Conn,
    type_: Annotated[TransactionType | None, Query(alias="type")] = None,
    page: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 1,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
):
    rows, pagination = transactions.list_transactions(conn, account_id, type_, page, limit)
    return {"data": rows, "pagination": pagination}


@router.post(
    "/transfers", status_code=status.HTTP_201_CREATED, response_model=TransferCreated,
    tags=["transfers"], responses=errors(400, 404, 409, 422),
)
def create_transfer(body: CreateTransferRequest, conn: Conn):
    return transfers.create_transfer(conn, body)


@router.get("/health", tags=["ops"])
def health(conn: Conn):
    conn.execute("SELECT 1")
    return {"status": "ok"}
