import asyncio
import time

import httpx
import pytest
from conftest import SECRET, instant, make_settings

from demo_server.api import create_app
from demo_server.auth import issue_token


@pytest.mark.asyncio
async def test_simulate_and_best_takedown_while_graph_mutates(tmp_path):
    app = create_app(make_settings(tmp_path), sleep=instant)
    eng = app.state.engine
    await eng.start()
    try:
        await eng.start_run(1, 20.0, "hero")
        await eng.join(60)
        run = eng.run
        base = [t for t in run.plan.scenario.txns][:20]
        tok = issue_token(SECRET, "officer")[0]
        hdr = {"Authorization": f"Bearer {tok}"}
        stop = time.monotonic() + 3.0
        errors: list[str] = []
        n = 0

        async def mutate() -> None:
            nonlocal n
            while time.monotonic() < stop:
                for t in base:
                    n += 1
                    run.ring.add_txn(
                        t.model_copy(
                            update={
                                "txn_id": f"mut-{n}",
                                "payee_hash": f"{n:064x}",
                                "payer_account_ref": None,
                                "payer_token": f"p{n % 50}",
                            }
                        ),
                        flagged=n % 7 == 0,
                    )
                await asyncio.sleep(0)

        async def hammer(c: httpx.AsyncClient) -> None:
            while time.monotonic() < stop:
                for r in (
                    await c.post("/api/ring/current/simulate", json={"k": 2}, headers=hdr),
                    await c.get("/api/ring/current/best-takedown?k=2", headers=hdr),
                    await c.get("/api/ring/current/graph", headers=hdr),
                ):
                    if r.status_code != 200:
                        errors.append(f"{r.status_code} {r.text[:80]}")

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            await asyncio.gather(mutate(), hammer(c), hammer(c))
        assert n > 100
        assert errors == []
    finally:
        await eng.aclose()
