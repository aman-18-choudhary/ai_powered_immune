import httpx
import pytest

from call_guard.api import create_app
from call_guard.model import Scorer


@pytest.fixture
async def client():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_score_scam(client):
    r = await client.post(
        "/score",
        json={
            "message": "You are under digital arrest. Do not tell anyone. Transfer to the RBI safe account.",
            "lang": "en",
        },
    )
    assert r.status_code == 200
    b = r.json()
    assert b["score"] >= 0.7 and b["reasons"] and b["model_version"] in ("clf-v1", "rules-v1")
    assert {"code", "weight", "detail"} <= set(b["reasons"][0])


async def test_score_benign_has_reasons_and_low_score(client):
    r = await client.post("/score", json={"message": "We will never ask for your OTP."})
    b = r.json()
    assert b["score"] < 0.2 and b["reasons"] and b["model_version"]


@pytest.mark.parametrize("msg", ["", "x" * 4001])
async def test_score_rejects_bad_length(client, msg):
    r = await client.post("/score", json={"message": msg})
    assert r.status_code == 422


async def test_score_accepts_4000(client):
    r = await client.post("/score", json={"message": "a" * 4000})
    assert r.status_code == 200


async def test_health(client):
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/readyz")).status_code == 200


async def test_stateless(client):
    m = {"message": "Do not tell anyone about this call."}
    a = (await client.post("/score", json=m)).json()
    b = (await client.post("/score", json=m)).json()
    assert a == b


async def test_whitespace_only_message_rejected(client):
    assert (await client.post("/score", json={"message": "   \n\t "})).status_code == 422


async def test_readyz_reports_model_version(client):
    body = (await client.get("/readyz")).json()
    assert body["status"] == "ready" and body["model_version"] in ("clf-v1", "rules-v1")


async def test_metrics_fallback_gauge():
    app = create_app(scorer=Scorer(None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        text = (await c.get("/metrics")).text
        assert "call_guard_fallback_mode 1" in text
        body = (await c.get("/readyz")).json()
        assert body["model_version"] == "rules-v1" and body["fallback_mode"] == "true"
