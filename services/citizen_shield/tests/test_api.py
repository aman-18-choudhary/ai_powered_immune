import asyncio
import hashlib
import logging

import httpx
import pytest
from scam_contracts.models import Complaint
from scam_contracts.topics import Topics
from svckit.bus import InMemoryBus

from citizen_shield.config import Settings
from citizen_shield.ratelimit import WindowLimiter
from citizen_shield.store import ReportStore, reports

from .conftest import BENIGN, HDR, KEY, SCAM, Down, client


def h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ----------------------------------------------------------------------------- /report
async def test_report_emits_a_complaint_and_stores_no_text(make_app):
    bus = InMemoryBus()
    app = make_app(bus=bus)
    async with client(app) as c:
        r = await c.post("/report", headers=HDR, json={
            "category": "scam_call", "message_hash": h(SCAM), "city_code": "MUM", "lang": "hi"})  # fmt: skip
    body = r.json()
    assert r.status_code == 200 and body["accepted"] and body["emitted"] and not body["duplicate"]
    (msg,) = bus.messages(Topics.COMPLAINTS)
    ev = Complaint.model_validate_json(msg[1])
    assert ev.complaint_id == body["report_id"] and ev.city_code == "MUM" and ev.kind == "scam_call"
    assert ev.amount_bucket is None and ev.case_ref is None
    assert h(SCAM) not in msg[1].decode() and h(SCAM) not in body["report_id"]
    with app.state.store.tx() as conn:
        rows = conn.execute(reports.select()).all()
    assert len(rows) == 1
    assert set(rows[0]._mapping) == {"report_id", "category", "city_code", "state_code",
                                     "created_at", "emitted"}  # fmt: skip
    assert h(SCAM) not in " ".join(str(v) for v in rows[0])


async def test_report_is_idempotent_on_message_hash(make_app):
    bus = InMemoryBus()
    app = make_app(bus=bus)
    payload = {"category": "scam_call", "message_hash": h("m"), "state_code": "TN"}
    async with client(app) as c:
        a = (await c.post("/report", headers=HDR, json=payload)).json()
        b = (
            await c.post("/report", headers=HDR | {"X-Principal-Sub": "other"}, json=payload)
        ).json()
        other = (
            await c.post("/report", headers=HDR, json=payload | {"message_hash": h("n")})
        ).json()
    assert a["report_id"] == b["report_id"] != other["report_id"]
    assert b["duplicate"] is True and b["emitted"] is True
    assert len(bus.messages(Topics.COMPLAINTS)) == 2 and app.state.store.count() == 2


async def test_report_hash_is_keyed(make_app):
    a = make_app(key=KEY)
    b = make_app(key=b"another-report-key-0123456789")
    payload = {"category": "scam_call", "message_hash": h("m"), "city_code": "DEL"}
    async with client(a) as ca, client(b) as cb:
        ia = (await ca.post("/report", headers=HDR, json=payload)).json()["report_id"]
        ib = (await cb.post("/report", headers=HDR, json=payload)).json()["report_id"]
    assert ia != ib and len(ia) == 52 and ia.startswith("rpt-")


async def test_report_without_location_is_stored_but_emits_nothing(make_app):
    bus = InMemoryBus()
    async with client(make_app(bus=bus)) as c:
        r = await c.post("/report", headers=HDR, json={"category": "held_transfer"})
    assert r.status_code == 200 and r.json()["emitted"] is False
    assert bus.messages(Topics.COMPLAINTS) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"category": "other"},
        {"category": "scam_call", "city_code": "Mumbai"},
        {"category": "scam_call", "state_code": "ZZ"},
        {"category": "scam_call", "message_hash": "not-a-hash"},
        {"category": "scam_call", "message_hash": "G" * 64},
        {"category": "scam_call", "lang": "x" * 40},
        {},
    ],
)
async def test_report_rejects_free_text_locations_and_bad_input(make_app, payload):
    async with client(make_app()) as c:
        r = await c.post("/report", headers=HDR, json=payload)
    assert r.status_code == 422
    for needle in ("Mumbai", "not-a-hash", "GGGG"):
        assert needle not in r.text


async def test_report_publish_failure_is_a_503_and_a_retry_emits(make_app):
    class FlakyBus(InMemoryBus):
        fail = True

        async def publish(self, topic, key, value):
            if self.fail:
                raise ConnectionError("broker down")
            await super().publish(topic, key, value)

    bus = FlakyBus()
    app = make_app(bus=bus)
    payload = {"category": "scam_call", "message_hash": h("m"), "city_code": "MUM"}
    async with client(app) as c:
        first = await c.post("/report", headers=HDR, json=payload)
        assert first.status_code == 503 and "broker" not in first.text
        assert app.state.counters["emit_failures"] == 1
        bus.fail = False
        second = await c.post("/report", headers=HDR, json=payload)
        third = await c.post("/report", headers=HDR, json=payload)
    assert second.status_code == 200 and second.json()["emitted"] and second.json()["duplicate"]
    assert third.json()["emitted"] is True
    assert len(bus.messages(Topics.COMPLAINTS)) == 1  # the retry emitted once; the third did not


async def test_report_store_failure_is_a_generic_503(make_app, mkstore):
    store = mkstore()
    app = make_app(store=store)
    async with client(app) as c:
        store.fail_next = 1
        r = await c.post("/report", headers=HDR, json={"category": "scam_call", "city_code": "MUM"})
    assert r.status_code == 503 and r.json() == {"detail": "store unavailable"}


async def test_report_needs_a_configured_key(make_app):
    async with client(make_app(key=None)) as c:
        r = await c.post("/report", headers=HDR, json={"category": "scam_call", "city_code": "MUM"})
    assert r.status_code == 503


# --------------------------------------------------------------------------- rate limits
async def test_rate_limit_is_per_principal_and_returns_retry_after(make_app):
    cfg = Settings(assess_per_min=3, report_per_min=2, peer_per_min=10**6)
    async with client(make_app(settings=cfg)) as c:
        codes = [(await c.post("/assess", json={"message": BENIGN}, headers=HDR)).status_code
                 for _ in range(5)]  # fmt: skip
        limited = await c.post("/assess", json={"message": BENIGN}, headers=HDR)
        other = await c.post(
            "/assess", json={"message": BENIGN}, headers=HDR | {"X-Principal-Sub": "c2"}
        )
        rep = [(await c.post("/report", headers=HDR, json={"category": "scam_call", "city_code": "MUM"})).status_code
               for _ in range(3)]  # fmt: skip
    assert codes == [200, 200, 200, 429, 429] and limited.headers["retry-after"].isdigit()
    assert other.status_code == 200 and rep == [200, 200, 429]


async def test_peer_limit_caps_total_traffic(make_app):
    cfg = Settings(assess_per_min=10**6, peer_per_min=2)
    async with client(make_app(settings=cfg)) as c:
        codes = [(await c.post("/assess", json={"message": BENIGN},
                               headers=HDR | {"X-Principal-Sub": f"s{i}"})).status_code for i in range(4)]  # fmt: skip
    assert codes == [200, 200, 429, 429]


async def test_limiter_failure_fails_open(make_app):
    class Broken(WindowLimiter):
        def check(self, key, limit):
            try:
                raise RuntimeError("limiter bug")
            except Exception:
                self.errors += 1
                return None

    lim = Broken()
    async with client(make_app(limiter=lim)) as c:
        r = await c.post("/assess", json={"message": BENIGN}, headers=HDR)
    assert r.status_code == 200 and lim.errors >= 1


def test_window_limiter_is_bounded_and_internal_errors_are_allowed():
    clock = {"t": 0.0}
    lim = WindowLimiter(max_keys=3, window_s=60, clock=lambda: clock["t"])
    for i in range(10):
        assert lim.check(f"k{i}", 1) is None
    assert len(lim._hits) == 3
    assert lim.check("k9", 1) is not None  # still tracked
    clock["t"] = 61.0
    assert lim.check("k9", 1) is None  # window reset

    def boom() -> float:
        raise RuntimeError

    bad = WindowLimiter(clock=boom)
    assert bad.check("x", 1) is None and bad.errors == 1  # fail open


# ------------------------------------------------------------------------ trust and misc
async def test_untrusted_and_wrong_role(make_app, monkeypatch):
    async with client(make_app()) as c:
        assert (
            await c.get("/languages", headers=HDR | {"X-Principal-Role": "officer"})
        ).status_code == 403
        assert (
            await c.get("/languages", headers={"X-Principal-Role": "citizen"})
        ).status_code == 401
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS")
    async with client(make_app()) as c:
        assert (await c.post("/assess", json={"message": "x"}, headers=HDR)).status_code == 401
        assert (await c.get("/healthz")).status_code == 200


async def test_gateway_secret_constant_time_and_non_ascii(make_app, monkeypatch):
    monkeypatch.setenv("CITIZEN_GATEWAY_SECRET", "s3cret-value")
    async with client(make_app()) as c:
        ok = await c.get("/languages", headers=HDR | {"X-Gateway-Secret": "s3cret-value"})
        weird = await c.get("/languages", headers=HDR | {"X-Gateway-Secret": "s3crét".encode()})
        none = await c.get("/languages", headers=HDR)
    assert ok.status_code == 200 and weird.status_code == none.status_code == 401


async def test_languages(make_app):
    async with client(make_app()) as c:
        r = await c.get("/languages", headers=HDR)
    assert [x["code"] for x in r.json()["supported"]] == ["en", "hi", "ta", "bn"]


async def test_body_cap_413_and_request_id_and_access_log(make_app, caplog):
    caplog.set_level(logging.INFO, logger="citizen_shield.access")
    async with client(make_app(settings=Settings(max_body_bytes=2048))) as c:
        big = await c.post("/assess", content=b"x" * 5000, headers=HDR)
        ok = await c.post("/assess?secret=1", json={"message": SCAM},
                          headers=HDR | {"X-Request-Id": "evil\r\nid"})  # fmt: skip
    assert big.status_code == 413
    assert ok.headers["x-request-id"] != "evil\r\nid" and len(ok.headers["x-request-id"]) == 12
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "route=/assess" in text and "secret" not in text and "CBI" not in text


async def test_metrics_gated(make_app, monkeypatch):
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS")
    monkeypatch.setenv("CITIZEN_METRICS_TOKEN", "tok-123")
    app = make_app(transport=Down(exc=httpx.ConnectError("x")))
    async with client(app) as c:
        assert (await c.get("/metrics")).status_code == 401
        weird = await c.get("/metrics", headers={"X-Metrics-Token": "tök".encode("latin-1")})
        monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")
        await c.post("/assess", json={"message": BENIGN}, headers=HDR)
        monkeypatch.delenv("TRUST_GATEWAY_HEADERS")
        ok = await c.get("/metrics", headers={"X-Metrics-Token": "tok-123"})
    assert weird.status_code == 401 and ok.status_code == 200
    assert "citizen_shield_assess_degraded_total 1" in ok.text
    assert 'citizen_shield_verdicts_total{label="suspicious"} 1' in ok.text
    assert "citizen_shield_guard_failures_total 1" in ok.text


async def test_readyz_follows_the_store_not_the_scorer(make_app, mkstore):
    store = mkstore()
    app = make_app(store=store, transport=Down(exc=httpx.ConnectError("x")))
    async with client(app) as c:
        r = await c.get("/readyz")
        assert r.status_code == 200 and r.json()["call_guard"] == "ok"
        store.ping = lambda: (_ for _ in ()).throw(ConnectionError("db"))  # type: ignore[method-assign]
        assert (await c.get("/readyz")).status_code == 503
        assert (await c.get("/healthz")).status_code == 200


async def test_lifespan_closes_guard_store_and_tasks(make_app, mkstore):
    store = mkstore()
    closed = []
    real = store.close
    store.close = lambda: (closed.append("store"), real())  # type: ignore[method-assign]
    app = make_app(store=store, background=True, settings=Settings(prune_interval_s=0.01))
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.03)
    assert closed == ["store"] and app.state.guard._http.is_closed


def test_service_app_requires_a_report_key(monkeypatch):
    from citizen_shield.api import create_service_app

    monkeypatch.delenv("CITIZEN_REPORT_KEY", raising=False)
    with pytest.raises(RuntimeError):
        create_service_app()
    monkeypatch.setenv("CITIZEN_REPORT_KEY", "k" * 32)
    monkeypatch.setenv("CALL_GUARD_URL", "http://example.invalid:9")
    monkeypatch.delenv("KAFKA_BOOTSTRAP", raising=False)
    app = create_service_app()
    assert app.state.guard._http.base_url == "http://example.invalid:9"
    app.state.store.close()


def test_settings_validation():
    with pytest.raises(ValueError):
        Settings(call_guard_timeout_s=0)
    assert ReportStore
