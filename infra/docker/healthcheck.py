"""Container HEALTHCHECK helper (stdlib only, no curl in the slim images).

Usage: python /usr/local/bin/healthcheck.py [PATH]   (port from $PORT, default 8000; PATH default /healthz)
Exit 0 when the endpoint answers 2xx within 2 s, else 1.
"""

import os
import sys
import urllib.request

path = sys.argv[1] if len(sys.argv) > 1 else "/healthz"
port = os.environ.get("PORT", "8000")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:  # noqa: S310
        sys.exit(0 if 200 <= r.status < 300 else 1)
except Exception:
    sys.exit(1)
