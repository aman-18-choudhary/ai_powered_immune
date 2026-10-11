#!/usr/bin/env python3
"""Build the hero evidence package through the demo-server code path and verify it, pinned.

Runs the demo-server app in-process (no network, pacing sleeps are skipped), plays the hero
scenario for SEED, opens a case, downloads the signed package and POSTs /api/verify, which runs
the package's own verify.py in an isolated interpreter pinned to the ledger's public key.

    python scripts/verify_package.py [SEED] [--out PATH.zip]

Exit 0 only when the verifier reports PASS. All data is simulator-generated.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from demo_server.api import create_app
from demo_server.config import Settings
from fastapi.testclient import TestClient


async def _instant(_: float) -> None:
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("seed", nargs="?", type=int, default=1)
    ap.add_argument("--out", type=Path, default=None, help="also save the package zip here")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(jwt_secret="verify-package-" + "x" * 40, work_dir=Path(tmp))
        with TestClient(create_app(settings, sleep=_instant)) as c:
            tok = c.post("/api/auth/demo-login", json={"role": "officer"}).json()["token"]
            h = {"Authorization": f"Bearer {tok}"}
            r = c.post("/api/demo/start", json={"seed": a.seed, "speed": 20, "mode": "hero"}, headers=h)
            r.raise_for_status()
            t0 = time.time()
            while True:
                st = c.get("/api/demo/state", headers=h).json()
                if st["status"] in ("finished", "error"):
                    break
                if time.time() - t0 > 120:
                    print("timeout waiting for the run")
                    return 2
                time.sleep(0.05)
            if st["status"] != "finished":
                print("run ended with status", st["status"])
                return 2
            case = c.post("/api/cases", json={}, headers=h)
            case.raise_for_status()
            cid = case.json()["case_id"]
            pkg = c.get(f"/api/packages/{cid}", headers=h)
            pkg.raise_for_status()
            if a.out:
                a.out.write_bytes(pkg.content)
            v = c.post("/api/verify", json={"case_id": cid}, headers=h)
            body = v.json()
    print(f"seed {a.seed}: case {cid}, package {len(pkg.content)} bytes, http {v.status_code}")
    for k, val in body.items():
        print(f"  {k}: {val}")
    ok = v.status_code == 200 and body.get("status") == "PASS_PINNED" and body.get("exit_code") == 0
    print("RESULT:", "PASS (pinned)" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
