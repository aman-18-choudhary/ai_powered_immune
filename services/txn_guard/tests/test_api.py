from pathlib import Path

import httpx

from txn_guard.api import create_app
from txn_guard.model import Scorer


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_readyz_reports_model_version():
    async with _client(create_app()) as c:
        r = await c.get("/readyz")
    assert r.status_code == 200
    assert r.json()["model_version"] == "gbm-v1" and r.json()["fallback_mode"] == "false"


async def test_readyz_and_metrics_report_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "tok")
    async with _client(create_app(Scorer(path=tmp_path / "nope.joblib"))) as c:
        assert (await c.get("/readyz")).json()["model_version"] == "rules-fallback-v1"
        assert (
            "txn_guard_fallback_mode 1"
            in (await c.get("/metrics", headers={"X-Metrics-Token": "tok"})).text
        )
