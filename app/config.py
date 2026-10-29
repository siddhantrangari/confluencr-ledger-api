import os

DEFAULT_DATABASE_URL = "postgresql://ledger:ledger@localhost:5433/ledger"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
