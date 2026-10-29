-- 001_init.sql — accounts + immutable transactions.
-- Every business rule that can be expressed as a constraint lives here, so the
-- database rejects bad data even if application code has a bug.

CREATE TABLE accounts (
    id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(100)  NOT NULL CHECK (length(btrim(name)) > 0),
    -- NUMERIC is exact decimal arithmetic (no float rounding). 18 digits, 2 after the point.
    -- DEFAULT 0 + the API never accepting a balance = "initial balance is always 0".
    balance     NUMERIC(18,2) NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    -- Last line of defence against overdraw: even a buggy code path cannot commit a negative balance.
    CONSTRAINT accounts_balance_non_negative CHECK (balance >= 0)
);

CREATE TABLE transactions (
    id            UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Internal, monotonically increasing number. Never exposed by the API; used only as a
    -- tie-breaker so "latest first" ordering is deterministic when two rows share created_at.
    seq           BIGINT        GENERATED ALWAYS AS IDENTITY UNIQUE,
    account_id    UUID          NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    type          VARCHAR(6)    NOT NULL CHECK (type IN ('credit', 'debit')),
    amount        NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    description   VARCHAR(255),
    reference_id  VARCHAR(64)   NOT NULL CHECK (length(reference_id) > 0),
    -- Set only on the two legs of a transfer, so they can be linked together.
    transfer_id   UUID,
    -- clock_timestamp() (wall clock at INSERT time) instead of now() (transaction start time):
    -- the insert happens after the account row lock is acquired, so per account, created_at
    -- order matches the order in which transactions were actually applied to the balance.
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT clock_timestamp(),
    -- Idempotency: one reference_id per account. Same reference_id on another account is fine.
    CONSTRAINT transactions_account_reference_unique UNIQUE (account_id, reference_id)
);

-- Serves the history query: WHERE account_id = ? [AND type = ?] ORDER BY created_at DESC, seq DESC.
CREATE INDEX transactions_history_idx
    ON transactions (account_id, created_at DESC, seq DESC);

-- Immutability enforced by the database, not just by "there is no PUT route".
CREATE FUNCTION reject_transaction_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'transactions are immutable: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER transactions_immutable
    BEFORE UPDATE OR DELETE ON transactions
    FOR EACH ROW EXECUTE FUNCTION reject_transaction_change();
