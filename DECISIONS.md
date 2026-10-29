# Design decisions

Short write-ups of the choices that matter, what I rejected, and why.

## 1. Stack: Python 3.12 + FastAPI + psycopg 3 + PostgreSQL 16

- **PostgreSQL over a NoSQL store.** A ledger needs multi-row ACID transactions, row-level locks, and declarative constraints (`CHECK`, `UNIQUE`, `FOREIGN KEY`, triggers). Postgres provides all of these natively. A document store would push those guarantees into application code, where they are much harder to prove.
- **FastAPI.** Request validation and the OpenAPI spec come from the same Pydantic models, so the documentation can't drift from the code. Swagger UI at `/docs` lets a reviewer try every endpoint.
- **psycopg 3 with plain SQL, no ORM.** Correctness here depends on exactly when `BEGIN`, `SELECT … FOR UPDATE`, `UPDATE` and `COMMIT` happen. With raw SQL every one of these is visible in the code. An ORM's unit-of-work, lazy loading and autoflush make it easy to read a stale balance or write it back in a separate statement without noticing. The application has 8 SQL statements, so an ORM would add more abstraction than it saves.
- **Sync endpoints, not async.** FastAPI runs sync endpoints in a thread pool, and each request borrows one pooled connection. Correctness comes from Postgres locks, not from Python's concurrency model, so the simpler sync code is the right trade. The tests run 20 truly parallel connections against the same account.

## 2. Money type

`NUMERIC(18,2)` in the database and `Decimal` in Python. Amounts are returned as JSON **strings**.

- **Floats** were rejected: binary floating point can't represent 0.1 exactly. There is a test that `0.1 + 0.2` is exactly `0.30`.
- **Integer minor units** (paise in a `BIGINT`) are equally correct and slightly faster. They push unit conversion onto every client, though (`10000` means ₹100), and the brief's examples are in major units. With `NUMERIC` the database stays readable and the API stays natural.
- **Strings in JSON** because many JSON parsers (JavaScript included) turn numbers into IEEE-754 doubles. A string preserves the exact value for any client.
- Input is limited to 2 decimal places and a maximum of 1,000,000,000.00. After validation every amount is normalised to 2 places.

## 3. Concurrency and locking

**The problem:** two debits of 60 against a balance of 100. Without coordination, both read 100, both pass the check, and the balance ends at −20. That is a lost update and an overdraw. Two copies of the same request arriving at the same moment have the same race.

**The solution: a pessimistic row lock on the account.** Each write runs in one DB transaction:

```
BEGIN
  SELECT … FROM accounts WHERE id = $1 FOR UPDATE   -- other writers for this account wait here
  SELECT … FROM transactions WHERE account_id = $1 AND reference_id = $2   -- duplicate?
  (debit) check balance >= amount
  INSERT INTO transactions …
  UPDATE accounts SET balance = balance + $delta     -- relative update, computed by Postgres
COMMIT
```

- The lock serialises writers **per account**. Different accounts never block each other.
- Isolation level is **READ COMMITTED** (the Postgres default), and that is enough here. When a waiting transaction gets the lock, `FOR UPDATE` returns the *latest committed* row. Each later statement takes a fresh snapshot, so the duplicate check sees a row committed by the transaction it waited for.
- **Alternatives considered:**
  - *A conditional update* (`UPDATE … SET balance = balance - $1 WHERE balance >= $1`) is also correct for a single debit. It doesn't cover the "check duplicate, then insert, then update" sequence as clearly, though. The explicit lock makes every step easy to reason about.
  - *SERIALIZABLE isolation* is correct but aborts conflicting transactions with serialization failures, so every caller needs retry logic. Under contention on one hot account it would mostly retry.
  - *Optimistic locking* with a version column suits low contention. It also needs a retry loop.
- **Defence in depth:** even if the locking code were removed, `CHECK (balance >= 0)` and `UNIQUE (account_id, reference_id)` still make Postgres refuse the bad write. The API maps those errors to 422 and 409.

**Transfers** lock two accounts. If A→B and B→A start at the same moment, each could hold one lock and wait for the other: a deadlock. To prevent that, the two account IDs are **always locked in sorted order**, so both transfers queue on the same first lock. A test runs 20 opposite-direction transfers in parallel and checks that there are no deadlocks and that the total amount of money is unchanged.

`created_at` uses `clock_timestamp()` (time of the INSERT, which happens after the lock is acquired), not `now()` (time the DB transaction started). Per account, history order therefore matches the order in which transactions were actually applied, even when a request waited for the lock.

## 4. Idempotency (`reference_id`)

- `UNIQUE (account_id, reference_id)` in the schema is the source of truth. The same reference on two different accounts is fine.
- The service checks for a duplicate **inside the lock, before the balance check**. Retrying a debit that already succeeded therefore returns "already exists", even if the balance has dropped since.
- **409 vs 200.** The brief lists "duplicate transaction" under error handling and asks for a response "indicating that the transaction already exists". A 409 says that unambiguously. The original transaction is in `details.existing_transaction`, so a client retrying after a network timeout still gets the result of its first attempt. (The Stripe style, 200 with a replayed response, is the other common convention, and switching to it is a small change.)
- Rejected requests store nothing, so their `reference_id` stays reusable. See ASSUMPTIONS #15.

## 5. Immutability

- **API:** there are no PUT, PATCH or DELETE routes for transactions. Any such request returns 404 or 405 in the standard error shape.
- **Database:** a `BEFORE UPDATE OR DELETE` trigger on `transactions` raises an exception. This protects the history from a future bug, a manual SQL session, or a script, not only from the API. Corrections are made the way accountants make them: with a new, compensating transaction.
- `accounts.balance` is the only mutable money field. It is changed in exactly one place (`apply_balance_change`), always in the same DB transaction as the INSERT that justifies it.

## 6. Atomicity

Each operation runs in one `with conn.transaction():` block, which issues `BEGIN` and then `COMMIT`, or `ROLLBACK` if any exception escapes. Connections run in autocommit mode outside these blocks, so transaction boundaries are always explicit.

Tests inject a failure **between** the INSERT and the balance UPDATE, and between the two legs of a transfer. In both cases nothing persists, the client gets a 500 that says nothing changed, and the same `reference_id` can be retried.

## 7. Errors

There is one error shape everywhere: `{"error": {"code", "message", "details", "request_id"}}`. Clients branch on `code`, which is stable, and show `message`. `request_id` is taken from the `X-Request-ID` header if present (otherwise generated) and is echoed in the response header for tracing.

| Status | When |
|---|---|
| 400 | Request is malformed or invalid (`VALIDATION_ERROR`, `INVALID_REQUEST_BODY`, `INVALID_AMOUNT`, `INVALID_TRANSACTION_TYPE`, `INVALID_PAGINATION`, `SAME_ACCOUNT_TRANSFER`) |
| 404 | Account (or route) doesn't exist |
| 409 | Duplicate `reference_id` |
| 422 | Valid request that breaks a business rule (`INSUFFICIENT_BALANCE`, `BALANCE_LIMIT_EXCEEDED`) |
| 500 | Unexpected; the transaction was rolled back |

Validation errors use 400, not FastAPI's default 422, so that 422 means only "breaks a business rule".

## 8. Pagination

Offset pagination (`page`, `limit`) because the brief asks for page numbers and total pages. The history query and the `COUNT(*)` run in one `REPEATABLE READ` read-only transaction, so `total_records` always matches the rows from the same snapshot. The index `(account_id, created_at DESC, seq DESC)` serves the query directly.

## 9. Testing strategy

Tests run against a real PostgreSQL instance, not mocks, because what needs proving (locks, constraints, triggers, rollback) only exists in the database. Coverage:

- every rule and every error case in the brief, through the HTTP API
- **concurrency**: 20 threads, each with its own connection, released together by a barrier. They cover parallel debits that would overdraw, parallel credits that would lose updates, 20 identical duplicates, opposite-direction transfers, and parallel transfers draining one source
- **atomicity**: failures injected mid-operation
- **constraints and triggers**, tested directly with SQL that bypasses the API

## Next steps (deliberately out of scope)

- **Auth and multi-tenancy:** JWT and account ownership checks. Right now any caller can act on any account.
- **Request fingerprinting:** store a hash of the request with each `reference_id` and return 422 when a reused key arrives with a different payload, instead of treating it as a duplicate.
- **Audit log of rejected attempts:** failed and declined transactions recorded in a separate table, for support and fraud analysis.
- **Double-entry ledger:** every movement becomes balanced entries across accounts (including system accounts for deposits and withdrawals), so `SUM(all entries) = 0` holds globally.
- **Hot accounts:** a single very busy account serialises on its row lock. Options at scale include sharded sub-balances, batching, or an append-only event log with an asynchronously derived balance.
- **Outbox and events:** write an event row in the same transaction and publish it to Kafka or RabbitMQ for notifications and analytics, without dual-write inconsistency.
- **Cursor pagination:** `(created_at, seq)` keyset pagination for deep pages and stable paging while new rows arrive.
- **Ops:** rate limiting, structured JSON logs, metrics (p95 latency, lock wait time), a least-privilege DB role without UPDATE/DELETE/TRUNCATE on `transactions`, and a migration tool such as Alembic or sqitch once the schema starts to change.
