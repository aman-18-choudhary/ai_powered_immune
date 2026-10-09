import asyncio
import hashlib
import io
import json
import logging
import zipfile

from scam_contracts.canonical import payload_hash
from scam_contracts.topics import Topics

from evidence_ledger.verify import verify_package

from .conftest import ADMIN, ANALYST, OFFICER, entry_in, hdr, tid


def seed(client, n=12, case_every=3):
    store = client.app.state.store
    for i in range(1, n + 1):
        refs = ["case-1", tid(i)] if i % case_every == 0 else [tid(i)]
        store.append(entry_in(i, refs=refs))
    return store


# ------------------------------------------------------------------ auth
async def test_untrusted_without_gateway_headers(client, monkeypatch):
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS")
    for path in ("/head", "/entries", "/entries/1", "/verify", "/keys", "/checkpoints/latest"):
        assert (await client.get(path, headers=OFFICER)).status_code == 401, path
    assert (await client.get("/healthz")).status_code == 200


async def test_gateway_secret_mode_and_non_ascii_secret(client, monkeypatch):
    monkeypatch.setenv("LEDGER_GATEWAY_SECRET", "s3cret")
    monkeypatch.setenv("TRUST_GATEWAY_HEADERS", "1")  # secret wins: the flag alone is not enough
    assert (await client.get("/head", headers=OFFICER)).status_code == 401
    ok = OFFICER | {"X-Gateway-Secret": "s3cret"}
    assert (await client.get("/head", headers=ok)).status_code == 200
    raw = [(b"x-gateway-secret", "séc".encode("latin-1")), (b"x-principal-role", b"officer"),
           (b"x-principal-sub", b"o1")]  # fmt: skip
    assert (await client.get("/head", headers=raw)).status_code == 401
    monkeypatch.setenv("LEDGER_GATEWAY_SECRET", "séc-ret")  # non-ASCII configured secret
    assert (await client.get("/head", headers=raw)).status_code == 401


async def test_roles(client):
    seed(client, 3)
    assert (await client.get("/entries", headers=ANALYST)).status_code == 200
    assert (await client.get("/verify", headers=ANALYST)).status_code == 403
    assert (await client.get("/packages/x", headers=ANALYST)).status_code == 403
    body = {"case_id": "c", "title": "t", "case_refs": ["r"]}
    assert (await client.post("/cases", json=body, headers=ANALYST)).status_code == 403
    assert (await client.get("/head", headers=hdr("bank"))).status_code == 403
    assert (await client.get("/head", headers={"X-Principal-Role": "officer"})).status_code == 401


async def test_principal_sub_must_be_pseudonymous(client):
    r = await client.get("/head", headers=hdr("officer", "Aman Duddy"))
    assert r.status_code == 403


# ------------------------------------------------------------------ reads
async def test_entries_head_pagination_and_bounds(client):
    seed(client, 7)
    h = (await client.get("/head", headers=OFFICER)).json()
    assert h["seq"] == 7 and h["count"] == 7
    r = (await client.get("/entries?from_seq=2&limit=3", headers=OFFICER)).json()
    assert [e["seq"] for e in r["items"]] == [2, 3, 4] and r["next_from_seq"] == 5
    r = (await client.get("/entries?from_seq=6&limit=10", headers=OFFICER)).json()
    assert [e["seq"] for e in r["items"]] == [6, 7] and r["next_from_seq"] is None
    assert (await client.get("/entries?limit=1001", headers=OFFICER)).status_code == 422
    assert (await client.get("/entries?limit=0", headers=OFFICER)).status_code == 422
    one = (await client.get("/entries/3", headers=OFFICER)).json()
    assert one["seq"] == 3 and one["ts"].endswith("Z") and one["payload"]["decision"]
    assert (await client.get("/entries/99", headers=OFFICER)).status_code == 404
    assert (await client.get("/entries/0", headers=OFFICER)).status_code == 422


async def test_empty_head(client):
    h = (await client.get("/head", headers=OFFICER)).json()
    assert h == {"seq": 0, "entry_hash": "0" * 64, "count": 0}


async def test_checkpoints_and_keys(client, keyring):
    seed(client, 12)
    assert (await client.get("/checkpoints/latest", headers=OFFICER)).json()["seq"] == 10
    r = (await client.get("/checkpoints?from_seq=6", headers=OFFICER)).json()
    assert [c["seq"] for c in r["items"]] == [10]
    k = (await client.get("/keys", headers=OFFICER)).json()
    assert k["keys"][0]["key_id"] == keyring.signer.key_id and k["keys"][0]["status"] == "current"
    assert "private" not in json.dumps(k).lower()


async def test_no_checkpoint_yet_is_404(client):
    assert (await client.get("/checkpoints/latest", headers=OFFICER)).status_code == 404


async def test_server_side_verify_and_tamper_detection(client):
    store = seed(client, 12)
    r = (await client.get("/verify", headers=OFFICER)).json()
    assert r["ok"] and r["checked"] == 12 and r["checkpoints_checked"] >= 1
    r = (await client.get("/verify?from_seq=4&to_seq=8", headers=OFFICER)).json()
    assert r["ok"] and r["checked"] == 5
    from sqlalchemy import text

    with store.engine.begin() as c:  # a DBA bypassing the triggers
        c.execute(text("drop trigger trg_ledger_entries_no_update"))
        c.execute(text("update ledger_entries set actor='mallory' where seq=7"))
    r = (await client.get("/verify", headers=OFFICER)).json()
    assert not r["ok"] and r["first_bad_seq"] == 7


async def test_verify_explicit_span(client):
    seed(client, 3)
    assert (await client.get("/verify?from_seq=1&to_seq=3", headers=OFFICER)).status_code == 200


# ------------------------------------------------------------------ privacy
async def test_422_never_echoes_input(client):
    secret = "9876543210"
    r = await client.post(
        "/cases",
        json={"case_id": "c", "title": f"call {secret}", "case_refs": ["a"]},
        headers=OFFICER,
    )
    assert r.status_code == 422 and secret not in r.text and "input" not in r.text
    r = await client.get(f"/entries/{secret}x", headers=OFFICER)
    assert r.status_code == 422 and secret not in r.text


async def test_no_query_strings_or_bodies_in_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    raw = "987654321098"
    await client.get(f"/entries?from_seq=1&limit=5&zz={raw}", headers=OFFICER)
    await client.post("/cases", json={"case_id": "c", "title": f"acct {raw}"}, headers=OFFICER)
    own = [r for r in caplog.records if r.name != "httpx"]
    text = "\n".join(r.getMessage() for r in own)
    assert raw not in text and "?" not in text
    assert any("route=/entries status=200" in r.getMessage() for r in own)


async def test_pii_guard_on_case_title_and_refs(client):
    for title in ("call +91 98765 43210", "pay bob@okaxis", "ABCDE1234F", "x" * 121):
        r = await client.post(
            "/cases", json={"case_id": "c1", "title": title, "case_refs": ["r"]}, headers=OFFICER
        )
        assert r.status_code == 422, title
    r = await client.post(
        "/cases",
        json={"case_id": "c1", "title": "ok", "case_refs": ["acct-123456789012"]},
        headers=OFFICER,
    )
    assert r.status_code == 422
    r = await client.post("/cases", json={"case_id": "c1", "title": "ok"}, headers=OFFICER)
    assert r.status_code == 422  # needs refs or seqs


async def test_metrics_gated(client, monkeypatch):
    seed(client, 2)
    assert (await client.get("/metrics")).status_code == 403  # trusted flag set, but no role
    r = await client.get("/metrics", headers=OFFICER)
    assert r.status_code == 200 and "evidence_ledger_appended_total 2" in r.text
    monkeypatch.setenv("LEDGER_METRICS_TOKEN", "tok")
    monkeypatch.delenv("TRUST_GATEWAY_HEADERS")
    assert (await client.get("/metrics", headers={"X-Metrics-Token": "tok"})).status_code == 200
    assert (await client.get("/metrics", headers={"X-Metrics-Token": "nope"})).status_code == 401


# ------------------------------------------------------------------ cases & packages
async def test_case_lifecycle_idempotent_and_immutable(client):
    seed(client)
    body = {"case_id": "case-1", "title": "Hero", "case_refs": ["case-1"]}
    r = await client.post("/cases", json=body, headers=OFFICER)
    assert r.status_code == 201 and r.json()["created_by"] == "officer-1"
    assert (await client.post("/cases", json=body, headers=ADMIN)).status_code == 200
    assert (
        await client.post("/cases", json=body | {"title": "other"}, headers=OFFICER)
    ).status_code == 409
    assert (await client.get("/cases/case-1", headers=OFFICER)).json()["case_refs"] == ["case-1"]
    assert (await client.get("/cases/nope", headers=OFFICER)).status_code == 404


async def test_package_download_audit_and_idempotent_export(client):
    store = seed(client)
    await client.post(
        "/cases",
        json={"case_id": "case-1", "title": "Hero", "case_refs": ["case-1"]},
        headers=OFFICER,
    )
    r1 = await client.get("/packages/case-1", headers=OFFICER)
    assert r1.status_code == 200 and r1.headers["content-type"] == "application/zip"
    assert (
        "attachment" in r1.headers["content-disposition"]
        and "case-1" in r1.headers["content-disposition"]
    )
    import hashlib

    sha = hashlib.sha256(r1.content).hexdigest()
    assert r1.headers["etag"] == f'"{sha}"'
    audit = store.page(13, 10)
    assert (
        len(audit) == 1
        and audit[0].event_type == "package.exported"
        and audit[0].actor == "officer-1"
    )
    assert audit[0].payload["package_sha256"] == sha and audit[0].payload["case_id"] == "case-1"
    assert (audit[0].payload["from_seq"], audit[0].payload["to_seq"]) == (3, 12)
    # repeated identical export: identical bytes, no new ledger entry
    r2 = await client.get("/packages/case-1", headers=OFFICER)
    assert r2.content == r1.content and store.head().seq == 13
    # another officer exporting the same package is recorded once (distinct exporter)
    await client.get("/packages/case-1", headers=hdr("officer", "officer-2"))
    assert store.head().seq == 14
    # the downloaded package verifies offline
    with zipfile.ZipFile(io.BytesIO(r1.content)) as z:
        assert "verify.py" in z.namelist()
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as t:
        Path(t, "p.zip").write_bytes(r1.content)
        res, _ = verify_package(Path(t, "p.zip"))
        assert res.ok, res


async def test_package_errors(client):
    seed(client)
    assert (await client.get("/packages/missing", headers=OFFICER)).status_code == 404
    await client.post(
        "/cases", json={"case_id": "ghost", "title": "t", "case_refs": ["nope"]}, headers=OFFICER
    )
    assert (await client.get("/packages/ghost", headers=OFFICER)).status_code == 404
    await client.post(
        "/cases", json={"case_id": "bad", "title": "t", "seqs": [999]}, headers=OFFICER
    )
    assert (await client.get("/packages/bad", headers=OFFICER)).status_code == 404


async def test_package_span_limit_is_413(client, tmp_path):
    from evidence_ledger.api import create_app

    app = create_app(
        database_url=f"sqlite:///{tmp_path}/small.db", keyring=client.app.state.keyring,
        checkpoint_every=5, maintenance_interval_s=0, max_package_span=5,
    )  # fmt: skip
    async with app.router.lifespan_context(app):
        import httpx

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            for i in range(1, 13):
                app.state.store.append(entry_in(i, refs=["wide"] if i in (1, 12) else []))
            await c.post(
                "/cases",
                json={"case_id": "wide", "title": "t", "case_refs": ["wide"]},
                headers=OFFICER,
            )
            r = await c.get("/packages/wide", headers=OFFICER)
            assert r.status_code == 413 and "narrow" in r.text
            assert app.state.store.head().seq == 12  # nothing was audited for a refused export


# ------------------------------------------------------------------ consumer
async def wait_for(pred, timeout=5.0):
    t = 0.0
    while t < timeout:
        if pred():
            return True
        await asyncio.sleep(0.02)
        t += 0.02
    return False


async def test_consumer_appends_dedupes_and_dlqs(client):
    store, bus = client.app.state.store, client.bus
    good = entry_in(1)
    await bus.publish(Topics.LEDGER, "k", good)
    await bus.publish(Topics.LEDGER, "k", good)  # at-least-once duplicate
    await bus.publish_raw(Topics.LEDGER, "k", b"{not json")
    bad_hash = good.model_copy(update={"payload_hash": "0" * 64})
    await bus.publish(Topics.LEDGER, "k", bad_hash)
    pii = {"note": "call 9876543210"}
    from scam_contracts.models import LedgerEntryIn

    await bus.publish(
        Topics.LEDGER, "k",
        LedgerEntryIn(service="s", actor="a", event_type="e", payload_hash=payload_hash(pii), payload=pii),
    )  # fmt: skip
    await bus.publish(Topics.LEDGER, "k", entry_in(2))
    assert await wait_for(lambda: store.head().seq == 4 and store.counters.snapshot()["dlq"] >= 1)
    es = store.page(1, 10)
    assert [e.event_type for e in es] == [
        "hold.created", "ledger.entry_quarantined.hash_mismatch",
        "ledger.entry_quarantined.pii", "hold.created",
    ]  # fmt: skip
    assert es[1].payload_hash == "0" * 64 and es[2].payload_hash == payload_hash(pii)
    snap = store.counters.snapshot()
    assert (snap["appended"], snap["duplicates"], snap["rejected"], snap["dlq"]) == (4, 1, 2, 1)
    dlq = bus.messages(Topics.LEDGER + Topics.DLQ_SUFFIX)  # only the unparseable message
    assert len(dlq) == 1 and dlq[0][1] == b"{not json"
    rows = store.dlq_events(10)
    assert len(rows) == 1 and rows[0]["raw_sha256"] == hashlib.sha256(b"{not json").hexdigest()
    assert "9876543210" not in json.dumps([e.model_dump(mode="json") for e in es])
    m = (await client.get("/metrics", headers=OFFICER)).text
    assert 'evidence_ledger_quarantined_total{reason="pii"} 1' in m
    assert 'evidence_ledger_quarantined_total{reason="hash_mismatch"} 1' in m
    assert "evidence_ledger_dlq_events_total 1" in m


async def test_case_with_txn_id_shaped_refs_is_not_rejected(client):
    for ref in ("txn_1234567890abcdef", "0123456789abcdef" * 4, "ab" * 16):
        body = {"case_id": "c-" + ref[:8], "title": "t", "case_refs": [ref]}
        assert (await client.post("/cases", json=body, headers=OFFICER)).status_code == 201, ref
    r = await client.post(
        "/cases",
        json={"case_id": "c9", "title": "t", "case_refs": ["acct-123456789012"]},
        headers=OFFICER,
    )
    assert r.status_code == 422


async def test_package_renders_quarantine_entries_as_withheld(client, keyring):
    store = client.app.state.store
    store.append(entry_in(1, refs=["case-q"]))
    q = store.append_or_quarantine(
        entry_in(2, payload={"note": "call 9876543210"}).model_copy(
            update={"payload_hash": payload_hash({"note": "call 9876543210"})}
        )
    )
    assert q.entry.event_type == "ledger.entry_quarantined.pii"
    await client.post(
        "/cases",
        json={"case_id": "case-q", "title": "t", "case_refs": ["case-q"], "seqs": [q.entry.seq]},
        headers=OFFICER,
    )
    r = await client.get("/packages/case-q", headers=OFFICER)
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        md = z.read("explanations.md").decode()
        assert "entry withheld by the ledger (pii)" in md and "9876543210" not in md
        assert b"9876543210" not in r.content


async def test_verify_detects_tail_truncation_via_retained_checkpoints(client):
    store = seed(client, 12)
    from sqlalchemy import text

    with store.engine.begin() as c:  # a DBA removes the triggers and deletes the tail entries
        for t in ("ledger_entries", "entry_refs"):
            c.execute(text(f"drop trigger trg_{t}_no_delete"))
        c.execute(text("delete from entry_refs where seq > 8"))
        c.execute(text("delete from ledger_entries where seq > 8"))
    r = (await client.get("/verify", headers=OFFICER)).json()
    assert not r["ok"] and r["first_bad_seq"] == 9 and "truncat" in r["reason"]


async def test_export_audit_carries_case_ref_and_history_endpoint(client):
    store = seed(client)
    await client.post(
        "/cases",
        json={"case_id": "case-1", "title": "Hero", "case_refs": ["case-1"]},
        headers=OFFICER,
    )
    r1 = await client.get("/packages/case-1", headers=OFFICER)
    audit = store.get(13)
    assert audit.case_refs == ["case-1"]
    # the export entry carries the case id as a ref but is NOT pulled into the case's own package
    r2 = await client.get("/packages/case-1", headers=hdr("officer", "officer-2"))
    assert r2.status_code == 200
    again = await client.get("/packages/case-1", headers=OFFICER)
    assert again.content == r1.content
    hist = (await client.get("/cases/case-1/exports", headers=OFFICER)).json()
    assert [e["actor"] for e in hist["items"]] == ["officer-1", "officer-2"]
    assert (await client.get("/cases/case-1/exports", headers=ANALYST)).status_code == 403
    assert (await client.get("/cases/nope/exports", headers=OFFICER)).status_code == 404


async def test_request_body_limit(client):
    big = {"case_id": "c", "title": "t", "case_refs": ["r"], "pad": "x" * 100_000}
    r = await client.post("/cases", json=big, headers=OFFICER)
    assert r.status_code == 413 and "x" * 20 not in r.text

    async def chunks():
        for _ in range(40):
            yield b"x" * 4096

    r = await client.post(
        "/cases", content=chunks(), headers=OFFICER | {"content-type": "application/json"}
    )
    assert r.status_code == 413  # no Content-Length: the streaming guard catches it
    ok = await client.post(
        "/cases", json={"case_id": "c", "title": "t", "case_refs": ["r"]}, headers=OFFICER
    )
    assert ok.status_code == 201
