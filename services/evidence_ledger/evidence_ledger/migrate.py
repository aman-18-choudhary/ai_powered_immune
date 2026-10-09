"""One-shot schema creation: ``python -m evidence_ledger.migrate`` (uses LEDGER_DATABASE_URL).

Creates tables and the append-only triggers. Safe to run twice and concurrently (on Postgres it
is serialised by an advisory lock). Run it with a schema-owner role; the service itself should
connect with an INSERT/SELECT-only role and ``LEDGER_CREATE_SCHEMA=0``.
"""

import os

from .store import LedgerStore

if __name__ == "__main__":
    store = LedgerStore(os.environ["LEDGER_DATABASE_URL"], create_schema=False)
    store.create_schema()
    store.close()
    print("evidence-ledger schema ready")
