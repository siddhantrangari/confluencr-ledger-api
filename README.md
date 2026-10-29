# Transaction Ledger API

A backend API for a simple transaction ledger. It supports accounts, credit and debit transactions with idempotent `reference_id`s, a filterable and paginated history, and atomic money transfers (the bonus).

**Stack:** Python 3.12 · FastAPI · PostgreSQL 16 · psycopg 3 (plain SQL, no ORM) · pytest

**What it guarantees, and how:**

| Guarantee | Enforced by |
|---|---|
| A balance never goes negative, even under concurrent debits | Row lock (`SELECT … FOR UPDATE`) and `CHECK (balance >= 0)` |
| A `reference_id` is used at most once per account, even with parallel retries | Duplicate check under the lock and `UNIQUE (account_id, reference_id)` |
| Inserting a transaction and updating the balance happen together or not at all | One DB transaction per operation |
| Transactions can never be edited or deleted | No such routes, plus a DB trigger that rejects `UPDATE`/`DELETE` |
| Transfers never deadlock and are never partial | Both accounts locked in sorted order, inside one DB transaction |
| No rounding errors | `NUMERIC(18,2)` and `Decimal`; amounts returned as strings |

## Run it locally

Prerequisites: Docker, and Python 3.12+.

```bash
# 1. Start Postgres (host port 5433; creates databases `ledger` and `ledger_test`)
docker compose up -d db

# 2. Install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# 3. Create the schema
python -m app.migrate

# 4. Start the API
uvicorn app.main:app --reload
```

Open **http://localhost:8000/docs** to try every endpoint in Swagger UI.

**To run without local Python**, the API itself can run in Docker:

```bash
docker compose --profile app up --build      # API on http://localhost:8000, migrations run on start
```

**Configuration:** `DATABASE_URL` (default `postgresql://ledger:ledger@localhost:5433/ledger`) and, for tests, `TEST_DATABASE_URL` (default `…/ledger_test`). The defaults match `docker-compose.yml`, so nothing needs to be set.

## Run the tests

```bash
pytest            # needs `docker compose up -d db` running; ~10 s
pytest -v         # see every test name
```

There are 109 tests, all run against a real PostgreSQL instance. They include parallel tests with 20 concurrent connections each.

> If you created the Postgres volume before `docker/create-test-db.sql` existed, the test database won't be there. Run `docker compose down -v && docker compose up -d db` to recreate it.

## Project layout

```
app/
  main.py              app factory, request-id middleware, error handlers (one error shape)
  routes.py            HTTP endpoints: validate -> call service -> shape response (no SQL)
  schemas.py           Pydantic request/response models (also generate the OpenAPI spec)
  errors.py            domain errors, each with an HTTP status and a stable error code
  db.py                connection pool; autocommit outside explicit transaction blocks
  migrate.py           tiny migration runner (applies migrations/*.sql once each)
  services/
    accounts.py        create / get / lock account
    transactions.py    create (lock -> duplicate check -> balance check -> insert -> update), history
    transfers.py       two-leg transfer with deterministic lock order
migrations/001_init.sql  tables, constraints, index, immutability trigger
tests/                   API, integrity, atomicity, concurrency and constraint tests
docs/API.md              endpoint reference with examples and every error code
docs/openapi.json        OpenAPI 3.1 spec (also served live at /openapi.json)
ASSUMPTIONS.md           every ambiguity in the brief and the decision taken
DECISIONS.md             design trade-offs: money type, locking, idempotency, immutability, next steps
```

## Documentation

- **[docs/API.md](docs/API.md)**: endpoints, request and response examples, and the full error-code table
- **[ASSUMPTIONS.md](ASSUMPTIONS.md)**: how each open question in the brief was resolved
- **[DECISIONS.md](DECISIONS.md)**: why it's built this way, the alternatives considered, and next steps

## Requirements → tests

| Brief requirement | Test(s) |
|---|---|
| Account: unique system ID, name required, balance starts at 0, client can't set balance | `test_accounts.py` |
| Credit adds, debit subtracts | `test_credit_increases_balance`, `test_debit_decreases_balance` |
| Debit with insufficient balance rejected, balance unchanged | `test_debit_with_insufficient_balance_is_rejected` |
| Duplicate `reference_id`: no new row, balance unchanged, "already exists" response | `test_duplicate_reference_creates_nothing_and_returns_existing` |
| Same `reference_id` allowed on another account | `test_same_reference_allowed_on_different_accounts` |
| Balance = result of all valid transactions (0 +100 +50 −30 −20 = 100) | `test_balance_equals_sum_of_valid_transactions` |
| Balance endpoint and 404 | `test_get_account_and_balance`, `test_balance_of_unknown_account_is_404` |
| History: type filter, pagination metadata, latest first | `test_history.py` |
| Invalid type / amount / pagination / body / account ID | `test_invalid_type`, `test_invalid_amount`, `test_invalid_pagination`, `test_malformed_json_body`, … |
| Immutability | `test_no_api_route_can_modify_transactions`, `test_database_rejects_update/delete_…` |
| Atomicity | `test_failure_after_insert_persists_nothing`, `test_failure_between_legs_rolls_back_whole_transfer` |
| Consistency under concurrency | `test_concurrency.py` (overdraw, lost update, duplicate race, deadlock) |
| Transfer: moves money, needs balance, both accounts exist, never partial | `test_transfers.py` |
