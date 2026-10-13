"""Stats, alerts, health, admin health, hardening and failure injection."""

import json
import logging

import httpx
import pytest
from conftest import PNG, bearer, create


async def test_empty_account_stats_zero(client, tok):
    r = await client.get("/me/stats", headers=tok("a"))
    s = r.json()
    assert r.status_code == 200 and s["total"] == 0 and s["last_complaint_at"] is None
    assert s["unread_alerts"] == 0
    assert set(s["by_status"]) == {
        "SUBMITTED",
        "UNDER_REVIEW",
        "INVESTIGATING",
        "RESOLVED",
        "REJECTED",
    }
    assert all(v == 0 for v in s["by_status"].values())
    assert "DIGITAL_ARREST" in s["by_category"] and all(v == 0 for v in s["by_category"].values())
    assert (await client.get("/alerts", headers=tok("a"))).json() == {
        "items": [],
        "total": 0,
        "unread": 0,
        "limit": 20,
        "offset": 0,
    }


async def test_stats_computed_from_db(client, tok, inv):
    a1 = await create(client, tok("a"), category="UPI_FRAUD")
    await create(client, tok("a"), category="UPI_FRAUD")
    await create(client, tok("a"), category="PHISHING")
    await create(client, tok("b"), category="PHISHING")
    await client.patch(
        f"/complaints/{a1['id']}/status", json={"status": "UNDER_REVIEW"}, headers=inv
    )
    d = await create(client, tok("a"))
    await client.delete(f"/complaints/{d['id']}", headers=tok("a"))
    s = (await client.get("/me/stats", headers=tok("a"))).json()
    assert s["total"] == 3
    assert s["by_status"]["SUBMITTED"] == 2 and s["by_status"]["UNDER_REVIEW"] == 1
    assert s["by_category"]["UPI_FRAUD"] == 2 and s["by_category"]["PHISHING"] == 1
    assert s["last_complaint_at"] and s["unread_alerts"] == 1


async def test_alert_read_flow_and_ownership(client, tok, inv):
    c = await create(client, tok("a"))
    await client.patch(f"/complaints/{c['id']}/status", json={"status": "REJECTED"}, headers=inv)
    al = (await client.get("/alerts", headers=tok("a"))).json()
    assert al["unread"] == 1
    aid = al["items"][0]["id"]
    assert (await client.post(f"/alerts/{aid}/read", headers=tok("b"))).status_code == 404
    r = await client.post(f"/alerts/{aid}/read", headers=tok("a"))
    assert r.status_code == 200 and r.json()["read_at"]
    again = await client.post(f"/alerts/{aid}/read", headers=tok("a"))
    assert again.status_code == 200 and again.json()["read_at"] == r.json()["read_at"]
    assert (await client.get("/alerts", headers=tok("a"))).json()["unread"] == 0
    assert (await client.get("/alerts?unread=true", headers=tok("a"))).json()["items"] == []
    assert (await client.get("/me/stats", headers=tok("a"))).json()["unread_alerts"] == 0
    assert (await client.post("/alerts/not-a-uuid/read", headers=tok("a"))).status_code == 404


async def test_alert_message_has_no_user_text(client, tok, inv):
    c = await create(client, tok("a"), title="My secret title 9876543210")
    await client.patch(
        f"/complaints/{c['id']}/status",
        json={"status": "REJECTED", "note": "internal note"},
        headers=inv,
    )
    al = (await client.get("/alerts", headers=tok("a"))).json()["items"][0]
    assert "secret" not in al["message"] and "internal" not in al["message"]


async def test_health_endpoints(client, tok, admin):
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    assert (await client.get("/readyz")).status_code == 200
    h = (await client.get("/admin/health", headers=admin)).json()
    assert h["db"]["status"] == "ok" and h["evidence_dir"]["status"] == "ok"
    assert h["jwks"]["status"] in ("configured", "injected")
    assert h["call_guard"]["status"] in ("importable", "unavailable")
    for k in ("qdrant", "neo4j", "postgis", "ollama", "redis"):
        assert h[k]["status"] == "not_configured"
    assert (await client.get("/admin/health", headers=tok("a"))).status_code == 403
    blob = json.dumps(h)
    assert "sk_" not in blob and "postgres" not in blob.casefold().replace("postgis", "")


async def test_readyz_503_when_db_down(client, monkeypatch):
    from complaints import store as store_mod

    def boom(self):
        raise store_mod.StoreError("db down")

    monkeypatch.setattr(store_mod.ComplaintStore, "ping", boom)
    r = await client.get("/readyz")
    assert r.status_code == 503 and "db down" not in r.text


async def test_db_down_is_503_without_detail(client, tok, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from complaints import store as store_mod

    def boom(self, *a, **k):
        raise OperationalError("SELECT secret FROM x", {}, Exception("password=hunter2 host=db"))

    monkeypatch.setattr(store_mod.ComplaintStore, "upsert_user", boom)
    r = await client.get("/complaints", headers=tok("a"))
    assert r.status_code == 503
    assert r.json() == {"error": "service_unavailable"}
    assert "hunter2" not in r.text and "SELECT" not in r.text


async def test_422_never_echoes_input(client, tok):
    secret = "SuperSecretValue-12345"
    r = await client.post(
        "/complaints",
        json={"category": secret, "title": secret, "description": 7},
        headers=tok("a"),
    )
    assert r.status_code == 422 and secret not in r.text
    r = await client.post("/complaints", content=b'{"title": "' + secret.encode(),
                          headers={**tok("a"), "Content-Type": "application/json"})  # fmt: skip
    assert r.status_code == 422 and secret not in r.text
    r = await client.get(f"/complaints?limit={secret}", headers=tok("a"))
    assert r.status_code == 422 and secret not in r.text


async def test_request_id_sanitised_and_echoed(client):
    r = await client.get("/healthz", headers={"X-Request-Id": "abc-123_ok"})
    assert r.headers["x-request-id"] == "abc-123_ok"
    r = await client.get("/healthz", headers={"X-Request-Id": "bad id\twith junk;<script>"})
    rid = r.headers["x-request-id"]
    assert rid != "bad id\twith junk;<script>" and len(rid) <= 64 and rid.replace("-", "").isalnum()
    r = await client.get("/healthz", headers={"X-Request-Id": "x" * 200})
    assert len(r.headers["x-request-id"]) <= 64


async def test_security_headers(client):
    r = await client.get("/healthz")
    h = r.headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer" and h["cache-control"] == "no-store"
    assert "default-src 'none'" in h["content-security-policy"]


async def test_cors_allowlist_only(client):
    ok = await client.options(
        "/complaints",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "authorization,idempotency-key"},
    )  # fmt: skip
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = await client.options(
        "/complaints",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in bad.headers
    assert ok.headers.get("access-control-allow-credentials") != "true"


async def test_logs_contain_no_tokens_emails_filenames_or_query(client, tok, caplog, idp):
    caplog.set_level(logging.DEBUG)
    t = idp.token(sub="log_user", email="very.private@example.org")
    h = bearer(t)
    c = await create(client, h, title="Private Title Words")
    await client.post(f"/complaints/{c['id']}/evidence",
                      files={"file": ("my-secret-name.png", PNG, "image/png")}, headers=h)  # fmt: skip
    await client.get("/complaints?q=topsecretquery", headers=h)
    await client.get("/complaints", headers=bearer(t[:-6] + "AAAAAA"))  # rejected token
    text = "\n".join(
        r.getMessage() for r in caplog.records if r.name.startswith("complaints")
    )  # httpx's own client logs show URLs; they are not this service
    for needle in (t, t.split(".")[1], "very.private", "my-secret-name", "topsecretquery",
                   "Private Title", "log_user", "Authorization"):  # fmt: skip
        assert needle not in text, needle
    assert "route=" in text  # the minimal access line is present


async def test_user_id_hash_in_access_log(client, tok, caplog):
    caplog.set_level(logging.INFO, logger="complaints.access")
    await client.get("/me/stats", headers=tok("a"))
    lines = [r.getMessage() for r in caplog.records if r.name == "complaints.access"]
    assert any("uid=" in line and "route=/me/stats" in line for line in lines)


async def test_lifespan_closes_engine_and_clients(make_app):
    app = make_app()
    async with app.router.lifespan_context(app):
        pass
    # disposing twice must not raise; engine is closed
    app.state.store.close()


async def test_blocking_db_calls_run_off_event_loop(client, tok, monkeypatch):
    import threading

    from complaints import store as store_mod

    seen = []
    real = store_mod.ComplaintStore.upsert_user

    def spy(self, *a, **k):
        seen.append(threading.current_thread() is threading.main_thread())
        return real(self, *a, **k)

    monkeypatch.setattr(store_mod.ComplaintStore, "upsert_user", spy)
    await client.get("/auth/session", headers=tok("a"))
    assert seen and not any(seen)


async def test_no_secret_columns_in_schema():
    from complaints.schema import metadata

    cols = {c.name.casefold() for t in metadata.tables.values() for c in t.columns}
    assert not {"password", "password_hash", "secret", "token", "api_key"} & cols


async def test_schema_constraints(settings):
    from datetime import UTC, datetime

    from sqlalchemy import insert
    from sqlalchemy.exc import IntegrityError

    from complaints.schema import users
    from complaints.store import ComplaintStore

    st = ComplaintStore(settings.database_url)
    try:
        with pytest.raises(IntegrityError), st.engine.begin() as c:
            c.execute(
                insert(users).values(
                    id="1",
                    idp_subject="s",
                    email_verified=False,
                    role="ROOT",
                    is_active=True,
                    created_at=datetime.now(UTC),
                )
            )
    finally:
        st.close()


def test_httpx_available():
    assert httpx.__version__


def test_rate_limiter_fails_open_on_internal_error():
    from complaints.api import RateLimiter

    assert RateLimiter({}).hit("unknown-kind", "u") is None  # KeyError inside -> allowed


async def test_chunked_json_body_capped(client, tok):
    async def gen():
        for _ in range(40):
            yield b" " * 4096

    r = await client.post(
        "/complaints", content=gen(), headers={**tok("a"), "Content-Type": "application/json"}
    )
    assert r.status_code == 413


async def test_unknown_field_name_not_echoed(client, tok):
    r = await client.post("/complaints", json={**{"category": "OTHER", "title": "abc",
                          "description": "long enough text"}, "leaky_key_name_77": 1},
                          headers=tok("a"))  # fmt: skip
    assert r.status_code == 422 and "leaky_key_name_77" not in r.text
