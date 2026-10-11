"""One-shot schema creation: ``python -m geo_intel.migrate`` (uses GEO_DATABASE_URL)."""

import os

from .store import GeoStore

if __name__ == "__main__":
    store = GeoStore(os.environ["GEO_DATABASE_URL"], create_schema=False)
    store.create_schema()
    store.close()
    print("geo-intel schema ready")
