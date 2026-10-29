# API reference

Base URL: `http://localhost:8000`. Interactive docs (Swagger UI) are at `/docs`. The machine-readable spec is [`openapi.json`](openapi.json).

- Request and response bodies are JSON.
- Money amounts are **strings** with 2 decimals (`"150.00"`). Requests may send a number (`150`) or a string (`"150.00"`).
- IDs are UUIDs. Timestamps are ISO-8601 in UTC.
- Optional request header `X-Request-ID`. It is echoed back in the response and inside error bodies.

## Endpoints

| Method | Path | Purpose | Success |
|---|---|---|---|
| POST | `/accounts` | Create an account (balance starts at 0) | 201 |
| GET | `/accounts/{account_id}` | Get an account | 200 |
| GET | `/accounts/{account_id}/balance` | Get the current balance | 200 |
| POST | `/accounts/{account_id}/transactions` | Add a credit or debit | 201 |
| GET | `/accounts/{account_id}/transactions` | Transaction history (filter, paginate, latest first) | 200 |
| POST | `/transfers` | Move money between two accounts atomically | 201 |
| GET | `/health` | Liveness and database check | 200 |

There are no update or delete endpoints for transactions, by design. Such requests return 404 or 405.

---

### POST /accounts

```json
{ "name": "Alice" }
```

| Field | Rules |
|---|---|
| `name` | required string, trimmed, 1–100 chars |

Any other field (`balance`, `id`, `created_at`, …) is rejected with 400.

**201**
```json
{
  "id": "c60bb65a-e4c4-4e3b-bcd1-b659b3912a12",
  "name": "Alice",
  "balance": "0.00",
  "created_at": "2026-10-29T10:48:09.427603Z"
}
```

**Errors:** 400 `VALIDATION_ERROR` (missing or blank name, unknown field), 400 `INVALID_REQUEST_BODY`.

---

### GET /accounts/{account_id}/balance

**200**
```json
{ "account_id": "c60bb65a-e4c4-4e3b-bcd1-b659b3912a12", "balance": "69.50" }
```

**Errors:** 404 `ACCOUNT_NOT_FOUND`, 400 `VALIDATION_ERROR` (ID is not a UUID).

`GET /accounts/{account_id}` returns the full account object, the same shape as the create response.

---

### POST /accounts/{account_id}/transactions

```json
{
  "type": "credit",
  "amount": 100,
  "reference_id": "inv-1001",
  "description": "Campaign payout"
}
```

| Field | Rules |
|---|---|
| `type` | required, `"credit"` or `"debit"` (lowercase) |
| `amount` | required, > 0, ≤ 1,000,000,000.00, at most 2 decimal places |
| `reference_id` | required, 1–64 chars, not blank, unique per account (case-sensitive) |
| `description` | optional, ≤ 255 chars |

**201** (the transaction, plus the account's new balance)
```json
{
  "transaction": {
    "id": "1ae2a1f3-ab7b-41bb-969d-70238f55abca",
    "account_id": "c60bb65a-e4c4-4e3b-bcd1-b659b3912a12",
    "type": "credit",
    "amount": "100.00",
    "description": "Campaign payout",
    "reference_id": "inv-1001",
    "transfer_id": null,
    "created_at": "2026-10-29T10:48:09.710204Z"
  },
  "account": { "account_id": "c60bb65a-e4c4-4e3b-bcd1-b659b3912a12", "balance": "100.00" }
}
```

**409 Duplicate.** The same `reference_id` was sent again for this account. Nothing is created, and the original transaction is returned:
```json
{
  "error": {
    "code": "DUPLICATE_TRANSACTION",
    "message": "A transaction with reference_id 'inv-1001' already exists for account c60bb65a-…; no new transaction was created",
    "details": { "existing_transaction": { "id": "1ae2a1f3-…", "type": "credit", "amount": "100.00", "reference_id": "inv-1001", "…": "…" } },
    "request_id": "83464168-7d23-42db-b9a4-ea3ec6d9a066"
  }
}
```

**422 Insufficient balance**
```json
{
  "error": {
    "code": "INSUFFICIENT_BALANCE",
    "message": "Insufficient balance: available 100.00, requested 150.00",
    "details": { "account_id": "c60bb65a-…", "balance": "100.00", "requested_amount": "150.00" },
    "request_id": "1e263c2c-0015-4bc6-8189-c813d5c776b2"
  }
}
```

**Other errors:** 404 `ACCOUNT_NOT_FOUND`, 400 `INVALID_TRANSACTION_TYPE`, 400 `INVALID_AMOUNT`, 400 `VALIDATION_ERROR`, 400 `INVALID_REQUEST_BODY`, 422 `BALANCE_LIMIT_EXCEEDED`.

---

### GET /accounts/{account_id}/transactions

| Query param | Default | Rules |
|---|---|---|
| `type` | (both) | `credit` or `debit` |
| `page` | 1 | integer, 1 – 1,000,000 |
| `limit` | 20 | integer, 1 – 100 |

Results are sorted latest first (`created_at DESC`, ties broken by insertion order).

`GET /accounts/{id}/transactions?type=debit&page=1&limit=1`

**200**
```json
{
  "data": [
    {
      "id": "4a9ffe2f-a729-4cba-83be-4c3d1434ae4b",
      "account_id": "c60bb65a-e4c4-4e3b-bcd1-b659b3912a12",
      "type": "debit",
      "amount": "30.50",
      "description": null,
      "reference_id": "pay-2",
      "transfer_id": null,
      "created_at": "2026-10-29T10:48:10.175951Z"
    }
  ],
  "pagination": { "page": 1, "limit": 1, "total_records": 1, "total_pages": 1 }
}
```

A page past the end returns `200` with `"data": []`.

**Errors:** 404 `ACCOUNT_NOT_FOUND`, 400 `INVALID_PAGINATION`, 400 `INVALID_TRANSACTION_TYPE`.

---

### POST /transfers

```json
{
  "from_account_id": "c60bb65a-e4c4-4e3b-bcd1-b659b3912a12",
  "to_account_id": "14ae088a-19c1-4d24-928b-8584c1efc8c3",
  "amount": 30,
  "reference_id": "tr-1",
  "description": "optional"
}
```

The debit, the credit and both balance updates happen in one database transaction: all of them or none. The `reference_id` must be unused on **both** accounts.

**201**
```json
{
  "transfer_id": "61e9ec44-13b0-4cbb-9bfe-1288bb3a9ccc",
  "reference_id": "tr-1",
  "amount": "30.00",
  "debit":  { "id": "6224cc53-…", "account_id": "c60bb65a-…", "type": "debit",  "amount": "30.00", "description": "Transfer to 14ae088a-…",   "reference_id": "tr-1", "transfer_id": "61e9ec44-…", "created_at": "…" },
  "credit": { "id": "8018688f-…", "account_id": "14ae088a-…", "type": "credit", "amount": "30.00", "description": "Transfer from c60bb65a-…", "reference_id": "tr-1", "transfer_id": "61e9ec44-…", "created_at": "…" },
  "from_account": { "account_id": "c60bb65a-…", "balance": "39.50" },
  "to_account":   { "account_id": "14ae088a-…", "balance": "30.00" }
}
```

**Errors:** 400 `SAME_ACCOUNT_TRANSFER`, 404 `ACCOUNT_NOT_FOUND` (`details.account_id` names the missing one), 409 `DUPLICATE_TRANSACTION`, 422 `INSUFFICIENT_BALANCE`, 400 `INVALID_AMOUNT` / `VALIDATION_ERROR`.

---

## Errors

Every error, including unknown routes and unexpected failures, has this shape:

```json
{
  "error": {
    "code": "INVALID_AMOUNT",
    "message": "Invalid request: amount: Input should be greater than 0",
    "details": [ { "field": "amount", "message": "Input should be greater than 0" } ],
    "request_id": "6295e626-0fb6-43ce-a175-6b4fc0d44c40"
  }
}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Missing or invalid field, unknown field (e.g. `balance` on create), malformed UUID, or several kinds of problems at once |
| 400 | `INVALID_REQUEST_BODY` | Body missing, not valid JSON, or not a JSON object |
| 400 | `INVALID_TRANSACTION_TYPE` | `type` is not `credit` or `debit` (body or query) |
| 400 | `INVALID_AMOUNT` | Amount ≤ 0, too large, more than 2 decimals, not a number |
| 400 | `INVALID_PAGINATION` | `page` / `limit` not an integer or out of range |
| 400 | `SAME_ACCOUNT_TRANSFER` | Transfer source and destination are the same account |
| 404 | `ACCOUNT_NOT_FOUND` | No account with that ID |
| 404 | `NOT_FOUND` | Unknown route |
| 405 | `METHOD_NOT_ALLOWED` | e.g. `DELETE /accounts/{id}/transactions` |
| 409 | `DUPLICATE_TRANSACTION` | `reference_id` already used on that account; `details.existing_transaction` holds the original |
| 422 | `INSUFFICIENT_BALANCE` | Debit or transfer larger than the available balance |
| 422 | `BALANCE_LIMIT_EXCEEDED` | Credit would push the balance past `NUMERIC(18,2)` capacity |
| 500 | `INTERNAL_ERROR` | Unexpected failure; the operation was rolled back and nothing changed |

## curl walkthrough

```bash
A=$(curl -s -X POST localhost:8000/accounts -H 'content-type: application/json' -d '{"name":"Alice"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
B=$(curl -s -X POST localhost:8000/accounts -H 'content-type: application/json' -d '{"name":"Bob"}'   | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

curl -s -X POST localhost:8000/accounts/$A/transactions -H 'content-type: application/json' \
  -d '{"type":"credit","amount":100,"reference_id":"inv-1"}'
curl -s -X POST localhost:8000/accounts/$A/transactions -H 'content-type: application/json' \
  -d '{"type":"credit","amount":100,"reference_id":"inv-1"}'          # 409 duplicate
curl -s -X POST localhost:8000/accounts/$A/transactions -H 'content-type: application/json' \
  -d '{"type":"debit","amount":150,"reference_id":"pay-1"}'           # 422 insufficient
curl -s -X POST localhost:8000/transfers -H 'content-type: application/json' \
  -d "{\"from_account_id\":\"$A\",\"to_account_id\":\"$B\",\"amount\":30,\"reference_id\":\"tr-1\"}"
curl -s localhost:8000/accounts/$A/balance                            # 70.00
curl -s "localhost:8000/accounts/$A/transactions?page=1&limit=10&type=debit"
```
