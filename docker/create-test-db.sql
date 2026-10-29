-- Runs once when the Postgres volume is first created: a separate database for the test suite,
-- so running tests never touches data you created by hand.
CREATE DATABASE ledger_test OWNER ledger;
