import asyncio
import logging
import threading
from datetime import timedelta

import pytest

from geo_intel.api import create_app
from geo_intel.config import Settings
from geo_intel.store import GeoStore

from .conftest import HDR, T0, client, complaint


def seed(app, items):
    for c in items:
        app.state.service.ingest(c)


def burst(city, n, kind="scam_call", end_min=2.0):
    return [complaint(T0 - timedelta(minutes=end_min + i * 0.1), city, kind) for i in range(n)]


async def test_hotspots_flags_a_surge_against_the_baseline(make_app):
    app = make_app()
    seed(app, burst("MUM", 8))  # 8 in the last hour, nothing before
    seed(app, [complaint(T0 - timedelta(hours=h), "DEL") for h in (2, 3, 4, 5, 6, 7)])
    async with client(app) as c:
        r = await c.get("/hotspots?window=1h", headers=HDR)
    body = r.json()
    assert r.status_code == 200 and body["model_version"] == "geo-poisson-v1"
    cells = {x["city_code"]: x for x in body["hotspots"]}
    assert cells["MUM"]["hotspot"] is True and cells["MUM"]["count"] == 8
    assert cells["MUM"]["trend"] == "up" and cells["DEL"]["hotspot"] is False
    assert body["hotspots"][0]["city_code"] == "MUM"
    assert body["as_of"] == (T0 - timedelta(minutes=2)).isoformat()  # newest complaint


async def test_hotspots_deterministic_etag_and_304(make_app):
    app = make_app()
    seed(app, burst("MUM", 4))
    async with client(app) as c:
        a = await c.get("/hotspots", headers=HDR)
        b = await c.get("/hotspots", headers=HDR)
        nm = await c.get("/hotspots", headers=HDR | {"If-None-Match": a.headers["etag"]})
        seed(app, burst("DEL", 1))
        changed = await c.get("/hotspots", headers=HDR | {"If-None-Match": a.headers["etag"]})
    assert a.content == b.content and a.headers["etag"] == b.headers["etag"]
    assert nm.status_code == 304 and nm.content == b""
    assert changed.status_code == 200 and changed.headers["etag"] != a.headers["etag"]


async def test_at_parameter_replays_a_past_moment(make_app):
    app = make_app()
    seed(app, burst("MUM", 5, end_min=300))  # five hours ago
    async with client(app) as c:
        now = (await c.get("/hotspots?window=1h", headers=HDR)).json()
        past = (
            await c.get(
                f"/hotspots?window=1h&at={(T0 - timedelta(minutes=295)).isoformat().replace('+', '%2B')}",
                headers=HDR,
            )
        ).json()
    assert now["as_of"] == (T0 - timedelta(minutes=300)).isoformat()
    assert {x["city_code"]: x["count"] for x in past["hotspots"]}["MUM"] == 5


async def test_cash_out_points_listed_separately(make_app):
    app = make_app()
    seed(app, [complaint(T0, "JMT", "cash_out"), complaint(T0, "JMT", "cash_out")])
    async with client(app) as c:
        body = (await c.get("/hotspots", headers=HDR)).json()
    assert body["hotspots"] == []
    (p,) = body["cash_out_points"]
    assert p["city_code"] == "JMT" and p["count"] == 2 and p["window_count"] == 2


@pytest.mark.parametrize("q", ["window=0s", "window=10", "window=<script>", "window=99999d",
                               "window=1h&at=yesterday", "window=1h&at=2026-03-10T12:00:00"])  # fmt: skip
async def test_invalid_query_is_422_and_never_echoed(make_app, q):
    async with client(make_app()) as c:
        r = await c.get(f"/hotspots?{q}", headers=HDR)
    assert r.status_code == 422
    for needle in ("script", "yesterday", "99999"):
        assert needle not in r.text


async def test_validation_handler_hides_input(make_app):
    async with client(make_app()) as c:
        r = await c.get("/points?limit=abc&before=secret-value", headers=HDR)
    assert r.status_code == 422 and "secret-value" not in r.text and "abc" not in r.text
    assert set(r.json()["detail"][0]) == {"loc", "msg", "type"}


async def test_points_pagination_is_bounded_and_newest_first(make_app):
    app = make_app()
    seed(app, [complaint(T0 - timedelta(minutes=i), "MUM", cid=f"pg-{i:08d}") for i in range(12)])
    async with client(app) as c:
        p1 = (await c.get("/points?limit=5", headers=HDR)).json()
        p2 = (await c.get(f"/points?limit=5&before={p1['next_before']}", headers=HDR)).json()
        p3 = (await c.get(f"/points?limit=5&before={p2['next_before']}", headers=HDR)).json()
        huge = await c.get("/points?limit=10000", headers=HDR)
        zero = await c.get("/points?limit=0", headers=HDR)
    seqs = [i["seq"] for i in p1["items"] + p2["items"] + p3["items"]]
    assert seqs == sorted(seqs, reverse=True) and len(set(seqs)) == 12
    assert p3["next_before"] is None and p1["count"] == p2["count"] == 5 and p3["count"] == 2
    assert huge.status_code == 200 and zero.status_code == 422
    item = p1["items"][0]
    assert "complaint_id" not in item and "case_ref" not in item
    assert set(item) == {"seq", "ts", "kind", "city_code", "city", "state", "state_code", "lat",
                         "lon", "precision", "amount_bucket"}  # fmt: skip


async def test_points_since_filter(make_app):
    app = make_app()
    seed(app, [complaint(T0 - timedelta(hours=2), cid="old-00000001"), complaint(T0)])
    async with client(app) as c:
        r = await c.get("/points", params={"since": (T0 - timedelta(hours=1)).isoformat()},
                        headers=HDR)  # fmt: skip
    assert r.json()["count"] == 1


async def test_cities_lists_the_static_table(make_app):
    async with client(make_app()) as c:
        r = await c.get("/cities", headers=HDR)
    items = r.json()["items"]
    assert (
        r.status_code == 200 and len(items) == 27 and items[0]["city_code"] < items[1]["city_code"]
    )
    assert {"city_code", "city", "state", "lat", "lon", "weight"} <= set(items[0])


# ----------------------------------------------------------------------------- trust gate
async def test_untrusted_requests_get_401_but_probes_are_open(make_app, monkeypatch):
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS")
    async with client(make_app()) as c:
        for path in ("/hotspots", "/points", "/cities"):
            assert (await c.get(path, headers=HDR)).status_code == 401
        assert (await c.get("/healthz")).status_code == 200
        assert (await c.get("/readyz")).status_code == 200


async def test_gateway_secret_is_constant_time_and_non_ascii_safe(make_app, monkeypatch):
    monkeypatch.setenv("GEO_GATEWAY_SECRET", "s3cret-value")
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS")
    async with client(make_app()) as c:
        ok = await c.get("/cities", headers=HDR | {"X-Gateway-Secret": "s3cret-value"})
        bad = await c.get("/cities", headers=HDR | {"X-Gateway-Secret": "nope"})
        none = await c.get("/cities", headers=HDR)
        weird = await c.get("/cities", headers=HDR | {"X-Gateway-Secret": "s3crét".encode()})
        raw = await c.get("/cities", headers={**HDR, "X-Gateway-Secret": "\u20ac".encode("utf-8")})
    assert ok.status_code == 200
    assert bad.status_code == none.status_code == weird.status_code == raw.status_code == 401


async def test_secret_set_overrides_trust_flag(make_app, monkeypatch):
    monkeypatch.setenv("GEO_GATEWAY_SECRET", "s3cret-value")  # TRUST_GATEWAY_HEADERS=1 still set
    async with client(make_app()) as c:
        assert (await c.get("/cities", headers=HDR)).status_code == 401


async def test_roles(make_app):
    async with client(make_app()) as c:
        for role in ("officer", "analyst", "admin"):
            r = await c.get("/cities", headers={**HDR, "X-Principal-Role": role})
            assert r.status_code == 200
        assert (
            await c.get("/cities", headers={**HDR, "X-Principal-Role": "citizen"})
        ).status_code == 403
        assert (await c.get("/cities", headers={"X-Principal-Role": "admin"})).status_code == 401


# ------------------------------------------------------------------------------ hardening
async def test_body_cap_413(make_app):
    app = make_app(settings=Settings(max_body_bytes=64))
    async with client(app) as c:
        r = await c.post("/hotspots", content=b"x" * 500, headers=HDR)
    assert r.status_code == 413


async def test_request_id_is_sanitised(make_app):
    async with client(make_app()) as c:
        good = await c.get("/healthz", headers={"X-Request-Id": "abcd-1234-efgh"})
        evil = await c.get("/healthz", headers={"X-Request-Id": "a\nb<script>x"})
    assert good.headers["x-request-id"] == "abcd-1234-efgh"
    assert (
        evil.headers["x-request-id"] != "a\nb<script>x" and len(evil.headers["x-request-id"]) == 12
    )


async def test_access_log_has_no_query_string_or_headers(make_app, caplog):
    caplog.set_level(logging.INFO, logger="geo_intel.access")
    async with client(make_app()) as c:
        await c.get(
            "/points?since=2026-03-10T00:00:00%2B00:00&limit=3",
            headers=HDR | {"X-Request-Id": "rid-12345678"},
        )
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "route=/points" in text and "status=200" in text and "rid=rid-12345678" in text
    assert "since" not in text and "limit" not in text and "2026" not in text and "u1" not in text


async def test_metrics_are_gated(make_app, monkeypatch):
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS")
    monkeypatch.setenv("GEO_METRICS_TOKEN", "tok-123")
    app = make_app()
    seed(app, burst("MUM", 2))
    async with client(app) as c:
        assert (await c.get("/metrics")).status_code == 401
        assert (await c.get("/metrics", headers={"X-Metrics-Token": "wrong"})).status_code == 401
        r = await c.get("/metrics", headers={"X-Metrics-Token": "tok-123"})
        weird = await c.get("/metrics", headers={"X-Metrics-Token": "tök".encode("latin-1")})
    assert r.status_code == 200 and "geo_intel_ingested_total 2" in r.text
    assert "geo_intel_rows 2" in r.text and weird.status_code == 401


async def test_metrics_via_gateway_trust_is_admin_only(make_app):
    async with client(make_app()) as c:
        for role in ("analyst", "officer", "citizen"):
            r = await c.get("/metrics", headers={**HDR, "X-Principal-Role": role})
            assert r.status_code == 403, role
        assert (
            await c.get("/metrics", headers={**HDR, "X-Principal-Role": "admin"})
        ).status_code == 200
        assert (await c.get("/metrics")).status_code == 401  # no principal at all


# ------------------------------------------------------------------ failure injection / loop
async def test_store_outage_is_a_generic_503_and_readyz_flips(make_app, mkstore):
    store = mkstore()
    app = make_app(store=store)
    async with client(app) as c:
        store.fail_next = 1
        r = await c.get("/hotspots", headers=HDR)
        assert r.status_code == 503 and r.json() == {"detail": "store unavailable"}
        assert "injected" not in r.text and "ConnectionError" not in r.text

        def boom() -> bool:
            raise ConnectionError("db down")

        store.ping = boom  # type: ignore[method-assign]
        assert (await c.get("/readyz")).status_code == 503
        assert (await c.get("/healthz")).status_code == 200


async def test_store_calls_run_off_the_event_loop(make_app, mkstore):
    store = mkstore()
    main = threading.get_ident()
    seen: set[int] = set()
    real = store.window_counts

    def spy(*a):
        seen.add(threading.get_ident())
        return real(*a)

    store.window_counts = spy  # type: ignore[method-assign]
    app = make_app(store=store)
    async with client(app) as c:
        await c.get("/hotspots", headers=HDR)
    assert seen and main not in seen


async def test_lifespan_closes_tasks_and_store(make_app, mkstore):
    store = mkstore()
    closed = []
    real_close = store.close
    store.close = lambda: (closed.append(1), real_close())  # type: ignore[method-assign]
    app = make_app(store=store, background=True, settings=Settings(prune_interval_s=0.01))
    async with app.router.lifespan_context(app):
        tasks = list(app.state.service.tasks)
        assert tasks and not any(t.done() for t in tasks)
        await asyncio.sleep(0.03)
    assert closed == [1] and all(t.done() for t in tasks) and app.state.service.tasks == []


def test_create_service_app_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GEO_DATABASE_URL", f"sqlite:///{tmp_path}/g.db")
    monkeypatch.setenv("GEO_RETENTION_DAYS", "3")
    monkeypatch.delenv("KAFKA_BOOTSTRAP", raising=False)
    from geo_intel.api import create_service_app

    app = create_service_app()
    assert app.state.service.cfg.retention_days == 3.0
    app.state.store.close()


def test_settings_reject_nonsense():
    with pytest.raises(ValueError):
        Settings(retention_days=0)
    with pytest.raises(ValueError):
        Settings(window_s=10, min_window_s=60)
    assert create_app and GeoStore


# ------------------------------------------------------------------- fix round 1
@pytest.mark.parametrize(
    "at",
    [
        "0001-01-01T00:00:00Z",
        "0001-01-01T00:00:00+14:00",
        "9999-12-31T23:59:59-14:00",
        "1969-12-31T23:59:59Z",
        "2100-01-01T00:00:01Z",
        "2026-03-10T12:00:00",
        "garbage",
        "0000-00-00T00:00:00Z",
    ],
)
async def test_extreme_or_malformed_timestamps_are_422_never_500(make_app, at):
    async with client(make_app()) as c:
        for path in ("/hotspots", "/points"):
            key = "at" if path == "/hotspots" else "since"
            r = await c.get(path, params={key: at}, headers=HDR)
            assert r.status_code == 422, (path, at, r.status_code)
            assert at not in r.text


async def test_boundary_timestamps_are_accepted(make_app):
    async with client(make_app()) as c:
        a = await c.get("/hotspots", params={"at": "1970-01-01T00:00:00Z"}, headers=HDR)
        b = await c.get(
            "/hotspots", params={"at": "2099-12-31T23:59:59Z", "window": "1d"}, headers=HDR
        )
    assert a.status_code == 200 and b.status_code == 200


async def test_future_dated_complaint_within_skew_cannot_move_as_of(make_app):
    app = make_app()  # clock = T0 = 12:00
    seed(app, [complaint(T0 - timedelta(minutes=m), "MUM") for m in (1, 2, 3, 4, 5)])
    seed(app, [complaint(T0 + timedelta(minutes=4), "DEL")])  # inside the 300 s default skew
    assert app.state.service.counters["dropped_future"] == 0
    async with client(app) as c:
        body = (await c.get("/hotspots?window=1h", headers=HDR)).json()
    assert body["as_of"] == T0.isoformat()
    assert {x["city_code"]: x["count"] for x in body["hotspots"]}["MUM"] == 5


async def test_one_hour_skew_setting_still_cannot_hide_hotspots(make_app):
    app = make_app(settings=Settings(future_skew_s=3600, min_count=3))
    seed(app, [complaint(T0 - timedelta(minutes=m), "MUM") for m in (1, 2, 3, 4, 5)])
    seed(app, [complaint(T0 + timedelta(minutes=59), "DEL")])
    async with client(app) as c:
        body = (await c.get("/hotspots?window=1h", headers=HDR)).json()
    assert body["as_of"] == T0.isoformat()
    assert {x["city_code"]: x["count"] for x in body["hotspots"]}["MUM"] == 5


def test_default_future_skew_is_five_minutes():
    assert Settings().future_skew_s == 300.0


def test_window_cap_is_clamped_so_the_baseline_fits_the_retention():
    cfg = Settings(retention_days=30, baseline_windows=6)
    assert cfg.max_window_s * (cfg.baseline_windows + 1) <= cfg.retention_s
    with pytest.raises(ValueError):
        Settings(retention_days=1, baseline_windows=6, window_s=20_000)  # window too big


async def test_window_above_the_clamped_cap_is_422(make_app):
    app = make_app(settings=Settings(retention_days=7, baseline_windows=6))
    async with client(app) as c:
        assert (await c.get("/hotspots?window=1d", headers=HDR)).status_code == 200
        assert (await c.get("/hotspots?window=2d", headers=HDR)).status_code == 422
