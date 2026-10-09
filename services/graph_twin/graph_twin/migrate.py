"""One-shot schema creation: ``python -m graph_twin.migrate`` (uses GRAPH_DATABASE_URL).

Run once per deploy, then start replicas with ``GRAPH_CREATE_SCHEMA=0``. The schema is
additive-only for this prototype; there is no in-place migration of old tables.
"""

import os

from .store import GraphStore

if __name__ == "__main__":
    store = GraphStore(os.environ["GRAPH_DATABASE_URL"], create_schema=False)
    store.create_schema()
    store.close()
    print("graph-twin schema ready")
