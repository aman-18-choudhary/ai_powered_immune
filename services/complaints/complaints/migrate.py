"""One-shot schema migration: ``python -m complaints.migrate`` (uses COMPLAINTS_DATABASE_URL).

Applies the pending versioned migrations of ``complaints.migrations`` (recorded in
``schema_migrations``) in one transaction; on Postgres it runs behind an advisory lock so replicas
starting together serialise. The service also runs this at startup. Prints versions only (never
the database URL).
"""

import os
import sys

from .store import ComplaintStore


def main() -> int:
    url = os.environ.get("COMPLAINTS_DATABASE_URL", "").strip()
    if not url:
        print("COMPLAINTS_DATABASE_URL must be set", file=sys.stderr)
        return 2
    store = ComplaintStore(url, create_schema=False)
    try:
        applied = store.migrate()
        version = store.schema_version()
    finally:
        store.close()
    done = ", ".join(str(v) for v in applied) if applied else "none"
    print(f"complaints schema ready (version {version}; applied: {done})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
