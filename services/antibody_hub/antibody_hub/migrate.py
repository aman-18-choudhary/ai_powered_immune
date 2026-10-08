"""One-shot schema creation: ``python -m antibody_hub.migrate`` (uses HUB_DATABASE_URL).

Run once per deploy, then start replicas with ``HUB_CREATE_SCHEMA=0``. Replicas that start with
the default ``HUB_CREATE_SCHEMA=1`` also create the schema, serialised by a Postgres advisory lock.
The schema is additive-only for this prototype; there is no in-place migration of old tables.
"""

import os

from .store import AntibodyStore

if __name__ == "__main__":
    store = AntibodyStore(os.environ["HUB_DATABASE_URL"], create_schema=False)
    store.create_schema()
    store.close()
    print("antibody-hub schema ready")
