"""Load check for the complaints API (Phase 14): many concurrent SYNTHETIC users against a
RUNNING server in demo/local mode; reports latency percentiles and errors per endpoint.

    DEMO_JWT_SECRET=<server secret> .venv/bin/python scripts/load_check.py \\
        --base http://127.0.0.1:18110 --users 40 --investigator investigator.e2e@example.org

The server must run with COMPLAINTS_AUTH_MODE=local (or demo) and the same DEMO_JWT_SECRET; the
investigator email must be in its INVESTIGATOR_ALLOWLIST. Each citizen files one complaint,
reads it, lists their complaints and runs one analysis; the investigator meanwhile reads the
queue, related and similar complaints and hotspots for those complaints. Every record is
SYNTHETIC and stays in the target server's database (use a throwaway server).

Numbers depend on the machine and on what else runs on it; they are a smoke test for
regressions and errors under concurrency, not a capacity figure. Exit 1 when any request
errors (5xx, timeouts) or a --max-p95-ms limit is exceeded.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

import httpx
import jwt

API = "/api/complaints"


def token(secret: str, sub: str, email: str) -> str:
    now = int(time.time())
    return jwt.encode({"sub": sub, "email": email, "email_verified": True, "iat": now,
                       "exp": now + 3600}, secret, algorithm="HS256")  # fmt: skip


class Recorder:
    def __init__(self) -> None:
        self.ms: dict[str, list[float]] = defaultdict(list)
        self.status: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.errors: dict[str, int] = defaultdict(int)

    async def call(self, c: httpx.AsyncClient, name: str, method: str, url: str, **kw):
        t0 = time.perf_counter()
        try:
            r = await c.request(method, API + url, **kw)
        except httpx.HTTPError:
            self.errors[name] += 1
            return None
        self.ms[name].append((time.perf_counter() - t0) * 1000)
        self.status[name][r.status_code] += 1
        if r.status_code >= 500:
            self.errors[name] += 1
        return r


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(len(s) - 1, round(p / 100 * (len(s) - 1)))]


async def citizen(c, rec, secret, i, run, ids):
    h = {"Authorization": f"Bearer {token(secret, f'load-{run}-{i}', f'load.{run}.{i}@example.org')}"}
    body = {"category": "UPI_FRAUD", "title": f"Load test {run} {i}",
            "description": "SYNTHETIC load test: a caller said he was from the bank and asked me "
                           "to approve a collect request and share the OTP to receive a refund.",
            "upi_id": f"load{i % 5}.{run}@ybl", "city": "Pune", "latitude": 18.52,
            "longitude": 73.85}  # fmt: skip
    r = await rec.call(c, "POST /complaints", "POST", "/complaints", json=body, headers=h)
    if r is None or r.status_code != 201:
        return
    cid = r.json()["id"]
    ids.append(cid)
    await rec.call(c, "GET /complaints/{id}", "GET", f"/complaints/{cid}", headers=h)
    await rec.call(c, "GET /complaints", "GET", "/complaints?limit=20", headers=h)
    await rec.call(c, "POST /complaints/{id}/analysis", "POST", f"/complaints/{cid}/analysis",
                   headers=h)  # fmt: skip


async def investigator(c, rec, secret, email, ids, stop):
    h = {"Authorization": f"Bearer {token(secret, 'load-investigator', email)}"}
    r = await rec.call(c, "POST /auth/entry", "POST", "/auth/entry",
                       json={"entry": "investigator"}, headers=h)  # fmt: skip
    if r is None or r.status_code != 200:
        print("investigator entry refused: is the email in INVESTIGATOR_ALLOWLIST?",
              file=sys.stderr)  # fmt: skip
        return
    n = 0
    while not stop.is_set() or n == 0:
        await rec.call(c, "GET /complaints (staff)", "GET", "/complaints?limit=20", headers=h)
        if ids:
            cid = ids[n % len(ids)]
            await rec.call(c, "GET related", "GET", f"/complaints/{cid}/related", headers=h)
            await rec.call(c, "GET similar", "GET", f"/complaints/{cid}/similar", headers=h)
        await rec.call(c, "GET hotspots", "GET", "/geo/complaints/hotspots", headers=h)
        n += 1


async def main_async(a) -> int:
    secret = os.environ.get("DEMO_JWT_SECRET", "")
    if len(secret.encode()) < 32:
        print("set DEMO_JWT_SECRET to the server's secret (32+ bytes)", file=sys.stderr)
        return 2
    run = uuid.uuid4().hex[:8]
    rec, ids, stop = Recorder(), [], asyncio.Event()
    limits = httpx.Limits(max_connections=a.users + 4)
    async with httpx.AsyncClient(base_url=a.base, timeout=a.timeout, limits=limits) as c:
        t0 = time.perf_counter()
        inv = asyncio.create_task(investigator(c, rec, secret, a.investigator, ids, stop))
        await asyncio.gather(*(citizen(c, rec, secret, i, run, ids) for i in range(a.users)))
        stop.set()
        await inv
        wall = time.perf_counter() - t0
    rows = []
    for name, xs in rec.ms.items():
        rows.append({"endpoint": name, "n": len(xs), "p50_ms": round(statistics.median(xs), 1),
                     "p95_ms": round(pct(xs, 95), 1), "p99_ms": round(pct(xs, 99), 1),
                     "max_ms": round(max(xs), 1), "status": dict(rec.status[name]),
                     "errors": rec.errors[name]})  # fmt: skip
    total_errors = sum(rec.errors.values())
    report = {"synthetic": True, "users": a.users, "wall_s": round(wall, 2),
              "complaints_created": len(ids), "errors": total_errors, "endpoints": rows}  # fmt: skip
    print(json.dumps(report, indent=2))
    if a.json_out:
        await asyncio.to_thread(
            Path(a.json_out).write_text, json.dumps(report, indent=2), "utf-8"
        )
    over = [r["endpoint"] for r in rows if a.max_p95_ms and r["p95_ms"] > a.max_p95_ms]
    if total_errors or over:
        print(f"FAIL: {total_errors} error(s); p95 over limit: {over}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--users", type=int, default=30)
    ap.add_argument("--investigator", required=True)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--max-p95-ms", type=float, default=0.0)
    ap.add_argument("--json-out")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
