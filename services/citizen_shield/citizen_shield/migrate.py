"""One-shot schema creation: ``python -m citizen_shield.migrate`` (uses CITIZEN_DATABASE_URL)."""

import os

from .store import ReportStore

if __name__ == "__main__":
    store = ReportStore(os.environ["CITIZEN_DATABASE_URL"], create_schema=False)
    store.create_schema()
    store.close()
    print("citizen-shield schema ready")
