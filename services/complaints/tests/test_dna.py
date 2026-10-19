"""Fraud DNA (Phase 8): privacy-filtered text, index lifecycle, similar-complaint API.

Every complaint here is synthetic TEST DATA created inside the test. The embedding model is the
deterministic ``FakeEmbedder`` (shared words = similar); the real model has its own opt-in test.
"""

import json
import os
import socket
from datetime import UTC, datetime

import pytest
from dna_fakes import FakeEmbedder

from complaints import dna as dna_mod
from complaints.dna import DnaIndex, FraudDna, dna_text, text_hash

ARREST = (
    "A caller said he was a CBI officer, kept me on a video call under digital arrest and made "
    "me transfer my savings to a safe account for verification."
)
ARREST_2 = (
    "Fake CBI officer on a video call said I was under digital arrest and made me transfer my "
    "savings to a safe account for verification of my money."
)
SHOES = (
    "My online shoe order arrived two weeks late in a damaged box and the seller ignores my "
    "messages about a replacement pair."
)


@pytest.fixture
def mk(client, tok):
    async def _mk(who: str = "a", description: str = ARREST, **fields) -> str:
        h = tok("a", sub=f"user_{who}", email=f"citizen.{who}@example.org")
        body = {"category": "DIGITAL_ARREST", "title": f"Report {who}",
                "description": description, **fields}  # fmt: skip
        r = await client.post("/complaints", json=body, headers=h)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    return _mk


def citizen(tok, who: str):
    return tok("a", sub=f"user_{who}", email=f"citizen.{who}@example.org")


async def similar(client, h, cid, **params):
    r = await client.get(f"/complaints/{cid}/similar", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def ids(res) -> list[str]:
    return [i["complaint"]["id"] for i in res["items"]]


# ------------------------------------------------------------------------------ text
def test_identifiers_are_redacted_before_embedding():
    t = dna_text(
        "Fraud via http://sbi-kyc.example/login",
        "Call from +91 98765 43210, paid Rs 49,999 to scam.mule@ybl, a/c 123456789012 "
        "IFSC SBIN0001234, mail a.b@example.org. hxxp://bad[.]example",
    )
    assert t is not None
    for raw in ("sbi-kyc", "98765", "mule", "123456789012", "SBIN0001234", "a.b@", "bad["):
        assert raw not in t
    assert "[link]" in t and "[address]" in t and "[ifsc]" in t and "[number]" in t
    assert "49,999" in t  # amounts are part of the method, not identifiers


def test_too_little_text_is_reported_not_embedded():
    assert dna_text("", "") is None
    assert dna_text("Help", "9876543210 http://x.example") is None
    hi = dna_text("डिजिटल अरेस्ट", "सीबीआई अधिकारी ने वीडियो कॉल पर पैसे मांगे")
    assert hi is not None  # Devanagari letters count


def test_text_hash_depends_on_model_and_text():
    assert text_hash("m1", "x") != text_hash("m2", "x") != text_hash("m1", "y")


# ------------------------------------------------------------------------------ access
async def test_staff_only(client, tok, inv, admin, mk):
    a = await mk("a")
    r = await client.get(f"/complaints/{a}/similar", headers=citizen(tok, "a"))
    assert r.status_code == 403  # even the owner: Fraud DNA is an investigator tool
    assert (await client.get(f"/complaints/{a}/similar")).status_code == 401
    assert (await client.get(f"/complaints/{a}/similar", headers=admin)).status_code == 200
    assert (await client.get("/complaints/nope/similar", headers=inv)).status_code == 404
    assert (await client.get(f"/complaints/{'x' * 65}/similar", headers=inv)).status_code == 404
    bad = await client.get(f"/complaints/{a}/similar", headers=inv, params={"limit": 0})
    assert bad.status_code == 422


# ------------------------------------------------------------------------------ results
async def test_similar_wording_is_found_and_unrelated_is_not(client, inv, mk):
    a = await mk("a", ARREST)
    b = await mk("b", ARREST_2)
    c = await mk("c", SHOES, category="OTHER")
    res = await similar(client, inv, a)
    assert res["status"] == "ok" and ids(res) == [b]
    item = res["items"][0]
    assert item["similarity"] >= dna_mod.SIMILARITY_THRESHOLD
    assert item["band"] in ("moderate", "strong")
    assert item["same_category"] is True and item["identifier_link"] is None
    assert "not proof" in res["disclaimer"]
    assert "worded alike" in item["explanation"]
    assert c not in ids(res) and a not in ids(res)  # never itself
    assert res["model"]["id"] == "test/fake-bow-64"
    # embeddings and texts are never returned
    blob = json.dumps(res)
    assert "vector" not in blob and "safe account" not in blob


async def test_identifier_link_and_shared_indicators_are_explained_separately(
    app, client, inv, mk
):
    a = await mk("a", ARREST, upi_id="mule.one@ybl")
    b = await mk("b", ARREST_2, upi_id="mule.one@ybl")
    now = datetime.now(UTC)
    for cid in (a, b):
        app.state.store.add_analysis(cid, {
            "pipeline_version": "test", "capabilities": {}, "status": "COMPLETED",
            "risk_score": 70, "risk_level": "HIGH", "classification": "DIGITAL_ARREST",
            "indicators": [{"code": "AUTHORITY_IMPERSONATION", "source": "rule"},
                           {"code": "MODEL_ONLY", "source": "model"}],
            "recommended_actions": [], "limitations": [],
        }, now)  # fmt: skip
    item = (await similar(client, inv, a))["items"][0]
    assert item["identifier_link"] == "observed"
    assert item["shared_indicators"] == ["AUTHORITY_IMPERSONATION"]  # rule-based only
    assert "share an identifier" in item["explanation"]


async def test_insufficient_text_and_limit(client, inv, mk):
    short = await mk("s", "Call 9876543210 http://x.example", category="OTHER")
    res = await similar(client, inv, short)
    assert res["status"] == "insufficient_text" and res["items"] == []
    a = await mk("a", ARREST)
    for w in ("b", "c", "d"):
        await mk(w, ARREST_2)
    res = await similar(client, inv, a, limit=2)
    assert len(res["items"]) == 2 and res["total"] == 3


# ------------------------------------------------------------------------------ privacy
async def test_opted_out_complaints_are_excluded_both_ways_and_immediately(
    app, client, inv, tok, mk
):
    a = await mk("a", ARREST)
    b = await mk("b", ARREST_2)
    assert ids(await similar(client, inv, a)) == [b]
    hb = citizen(tok, "b")
    r = await client.patch("/me", json={"privacy": {"allow_case_matching": False}}, headers=hb)
    assert r.status_code == 200
    res = await similar(client, inv, a)
    assert ids(res) == [] and res["total"] == 0  # not shown and not counted
    own = await similar(client, inv, b)
    assert own["matching"] == "disabled" and own["items"] == []
    assert "complainant" in own["matching_message"]
    # removed from the index itself, not only filtered
    dna = app.state.dna
    assert b not in dna.index.hashes()
    r = await client.patch("/me", json={"privacy": {"allow_case_matching": True}}, headers=hb)
    assert ids(await similar(client, inv, a)) == [b]


async def test_request_time_recheck_even_if_the_index_is_stale(app, client, inv, tok, mk):
    a = await mk("a", ARREST)
    b = await mk("b", ARREST_2)
    assert ids(await similar(client, inv, a)) == [b]
    # opt out behind the service's back (no notify): the index still holds b
    import sqlalchemy as sa

    from complaints.schema import users

    with app.state.store.engine.begin() as conn:
        conn.execute(sa.update(users).where(users.c.email == "citizen.b@example.org")
                     .values(allow_case_matching=False))  # fmt: skip
    assert b in app.state.dna.index.hashes()
    res = await similar(client, inv, a)
    assert ids(res) == [] and res["total"] == 0


async def test_deleted_complaints_are_excluded_and_removed(app, client, inv, tok, mk):
    a = await mk("a", ARREST)
    b = await mk("b", ARREST_2)
    assert ids(await similar(client, inv, a)) == [b]
    r = await client.delete(f"/complaints/{b}", headers=citizen(tok, "b"))
    assert r.status_code == 204
    assert ids(await similar(client, inv, a)) == []
    assert b not in app.state.dna.index.hashes()
    assert (await client.get(f"/complaints/{b}/similar", headers=inv)).status_code == 404


async def test_embedder_receives_redacted_text_and_identifiers_never_logged(
    app, client, inv, mk, caplog
):
    caplog.set_level("DEBUG")
    a = await mk("a", ARREST + " He called from 9876543210 and used mule.x@ybl.")
    await similar(client, inv, a)
    sent = json.dumps(app.state.dna.embedder.calls)
    assert "9876543210" not in sent and "mule.x" not in sent and "[number]" in sent
    assert "9876543210" not in caplog.text and "digital arrest" not in caplog.text


# ------------------------------------------------------------------------------ failures
async def test_unavailable_engine_is_reported_not_faked(make_app, inv_for):
    for reason in ("engine_not_installed", "model_not_available_offline"):
        app = make_app(dna_embedder=FakeEmbedder(reason=reason))
        client, h, create = await inv_for(app)
        a = await create()
        res = (await client.get(f"/complaints/{a}/similar", headers=h)).json()
        assert res["status"] == "unavailable" and res["reason"] == reason
        assert res["items"] == [] and res["message"]
        models = (await client.get("/models", headers=h)).json()
        assert models["dna"]["status"] == "unavailable" and models["dna"]["reason"] == reason


async def test_embedding_failure_and_disabled_config(make_app, settings, inv_for):
    import dataclasses

    app = make_app(dna_embedder=FakeEmbedder(fail=True))
    client, h, create = await inv_for(app)
    a = await create()
    res = (await client.get(f"/complaints/{a}/similar", headers=h)).json()
    assert res["status"] == "unavailable" and res["reason"] == "embedding_failed"
    off = make_app(settings=dataclasses.replace(settings, dna_enabled=False))
    client, h, create = await inv_for(off)
    a = await create()
    res = (await client.get(f"/complaints/{a}/similar", headers=h)).json()
    assert res["status"] == "unavailable" and res["reason"] == "disabled"


@pytest.fixture
def inv_for(tok):
    """(client, investigator headers, create-a-complaint coroutine) for an app built in a test."""
    import httpx

    async def _for(app):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        h = tok("inv")
        r = await client.post("/auth/entry", json={"entry": "investigator"}, headers=h)
        assert r.status_code == 200

        async def create() -> str:
            r = await client.post("/complaints", headers=tok("a"), json={
                "category": "DIGITAL_ARREST", "title": "Report", "description": ARREST})  # fmt: skip
            assert r.status_code == 201, r.text
            return r.json()["id"]

        return client, h, create

    return _for


# ------------------------------------------------------------------------------ index
async def test_sync_is_idempotent_and_payload_holds_no_text(app, client, inv, mk):
    a = await mk("a", ARREST)
    await mk("b", ARREST_2)
    dna = app.state.dna
    first = await dna.sync()
    again = await dna.sync()
    assert first["indexed"] == 2 and again["added_or_updated"] == 0 and again["removed"] == 0
    pts, _ = dna.index._client.scroll(dna.index.collection, limit=10, with_payload=True)
    assert {k for p in pts for k in (p.payload or {})} == {"complaint_id", "text_hash"}
    assert a in dna.index.hashes()


async def test_index_on_disk_survives_restart_and_model_change_uses_new_collection(
    app, tmp_path
):
    store = app.state.store
    path = tmp_path / "dna"
    d1 = FraudDna(store, FakeEmbedder(), path, background=False)
    await d1.sync()
    name = d1.index.collection
    await d1.close()
    d2 = FraudDna(store, FakeEmbedder(), path, background=False)
    assert (await d2.sync())["added_or_updated"] == 0  # nothing new: same collection reused
    assert d2.index.collection == name
    await d2.close()
    other = FakeEmbedder(dim=32)
    d3 = FraudDna(store, other, path, background=False)
    await d3.sync()
    assert d3.index.collection != name and d3.index.collection.endswith("__32")
    await d3.close()


def test_collection_name_is_deterministic():
    n = DnaIndex.collection_name("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", 384)
    assert n == ("complaint_dna__dna_text_v1__sentence_transformers_paraphrase_multilingual_"
                 "minilm_l12_v2__384")  # fmt: skip


# ------------------------------------------------------------------------------ real model
@pytest.mark.skipif(os.getenv("COMPLAINTS_TEST_REAL_DNA") != "1",
                    reason="set COMPLAINTS_TEST_REAL_DNA=1 (needs the 'dna' extra and the cached model)")  # fmt: skip
def test_real_model_offline_and_meaningful(monkeypatch):
    from complaints.dna import SentenceTransformerEmbedder

    def no_network(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    e = SentenceTransformerEmbedder()
    e.load()
    assert e.dim == 384 and e.status()["status"] == "ready" and e.status()["revision"]
    hi = dna_text("डिजिटल अरेस्ट", "सीबीआई अधिकारी बनकर वीडियो कॉल पर डिजिटल अरेस्ट की धमकी दी और "
                  "सुरक्षित खाते में पैसे ट्रांसफर करवाए")  # fmt: skip
    v = e.embed([ARREST, ARREST_2, SHOES, hi])

    def cos(x, y):
        return sum(p * q for p, q in zip(x, y, strict=True))

    assert cos(v[0], v[1]) > cos(v[0], v[2]) + 0.2
    assert cos(v[0], v[3]) > cos(v[3], v[2])


async def test_repeated_panel_loads_are_not_rate_limited_like_analysis_runs(client, inv, mk):
    # the panel polls while the model loads; 15 quick loads stay within the read bucket
    a = await mk("a", ARREST)
    codes = [(await client.get(f"/complaints/{a}/similar", headers=inv)).status_code
             for _ in range(15)]  # fmt: skip
    assert codes == [200] * 15
