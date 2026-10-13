"""One-shot schema creation: ``python -m complaints.migrate`` (uses COMPLAINTS_DATABASE_URL).

``create_all`` is idempotent and, on Postgres, runs behind an advisory lock so replicas starting
together serialise. This is create-only: there is no in-place migration of existing tables.
"""

import os

from .store import ComplaintStore

if __name__ == "__main__":
    store = ComplaintStore(os.environ["COMPLAINTS_DATABASE_URL"], create_schema=False)
    store.create_schema()
    store.close()
    print("complaints schema ready")
