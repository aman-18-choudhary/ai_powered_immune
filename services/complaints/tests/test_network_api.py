"""Phase 9 network API: related complaints, graph, clusters, identifier search and link reviews.

Every complaint and identifier here is synthetic TEST DATA created inside the test (made-up UPI
ids, phone numbers, accounts and example.* domains).
"""

import json
import logging

import pytest
import sqlalchemy as sa

from complaints import network_store

DISCLAIMER = "Shared identifiers are leads, not proof that the same person is involved."


@pytest.fixture
def mk(client, tok):
    """Create a test complaint as citizen ``who`` (a, b or any extra name)."""

    async def _mk(who: str = "a", **fields) -> str:
        h = tok("a", sub=f"user_{who}", email=f"citizen.{who}@example.org")
        body = {
            "category": "UPI_FRAUD",
            "title": f"Test complaint {who}",
            "description": "Synthetic test complaint for the network tests.",
            **fields,
        }
        r = await client.post("/complaints", json=body, headers=h)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    return _mk


def citizen(tok, who: str):
    return tok("a", sub=f"user_{who}", email=f"citizen.{who}@example.org")


async def related(client, h, cid, **params):
    r = await client.get(f"/complaints/{cid}/related", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def review(client, h, a, b, decision, note=None):
    body = {"complaint_a": a, "complaint_b": b, "decision": decision}
    if note is not None:
        body["note"] = note
    return await client.post("/complaint-links/review", json=body, headers=h)


# ------------------------------------------------------------------------------ access
async def test_citizens_get_403_and_anonymous_401(client, tok, mk):
    cid = await mk("a", upi_id="mule.one@ybl")
    h = citizen(tok, "a")  # the owner, still a citizen
    calls = [
        client.get(f"/complaints/{cid}/related", headers=h),
        client.get(f"/complaints/{cid}/graph", headers=h),
        client.get("/graph/clusters", headers=h),
        client.post("/entities/search", json={"type": "upi", "value": "mule.one@ybl"}, headers=h),
        client.post(
            "/complaint-links/review",
            json={"complaint_a": cid, "complaint_b": cid + "x", "decision": "confirmed"},
            headers=h,
        ),
    ]
    for coro in calls:
        r = await coro
        assert r.status_code == 403 and r.json()["error"] == "forbidden_role", r.text
    r = await client.get(f"/complaints/{cid}/related")
    assert r.status_code == 401


async def test_unknown_ids_are_404(client, inv):
    for path in ("/complaints/nope/related", "/complaints/nope/graph"):
        r = await client.get(path, headers=inv)
        assert r.status_code == 404 and r.json()["error"] == "not_found"
    r = await client.get("/complaints/" + "x" * 80 + "/related", headers=inv)
    assert r.status_code == 404


# ------------------------------------------------------------------------------ links
async def test_no_link_without_a_shared_identifier(client, inv, mk):
    a = await mk("a", upi_id="mule.one@ybl", suspect_phone="9876543210")
    await mk("b", upi_id="mule.two@ybl", suspect_phone="9876543211")
    await mk("c")  # no identifiers at all
    out = await related(client, inv, a)
    assert out["items"] == [] and out["total"] == 0
    assert out["linking"] == "enabled" and out["disclaimer"] == DISCLAIMER


async def test_observed_link_on_the_same_normalised_upi(client, inv, mk):
    a = await mk("a", upi_id="Mule.One@YBL")
    b = await mk("b", upi_id="mule.one@ybl", suspect_phone="9876543210")
    out = await related(client, inv, a)
    assert out["total"] == 1
    (item,) = out["items"]
    assert item["complaint"]["id"] == b
    assert set(item["complaint"]) >= {
        "id",
        "title",
        "category",
        "status",
        "risk_level",
        "created_at",
    }
    assert item["link_type"] == "observed" and item["basis"] == "observed"
    assert item["shared"] == [
        {
            "type": "upi",
            "display": "mu•••@ybl",
            "link": "observed",
            "this_field": "upi_id",
            "other_field": "upi_id",
        }
    ]
    assert item["review"] is None
    assert "mule.one@ybl" not in json.dumps(out).casefold()


async def test_same_domain_different_url_is_inferred_and_same_url_is_observed(client, inv, mk):
    a = await mk("a", suspicious_url="https://kyc.example.co.in/verify")
    b = await mk("b", suspicious_url="https://pay.example.co.in/refund?x=1")
    c = await mk("c", suspicious_url="HTTPS://KYC.example.co.in:443/verify#frag")
    out = await related(client, inv, a)
    by_id = {i["complaint"]["id"]: i for i in out["items"]}
    assert by_id[b]["link_type"] == "inferred"
    assert by_id[b]["shared"] == [
        {
            "type": "domain",
            "display": "example.co.in",
            "link": "inferred",
            "this_field": "suspicious_url",
            "other_field": "suspicious_url",
        }
    ]
    # identical URL: observed, and the implied domain is not listed again
    assert by_id[c]["link_type"] == "observed"
    assert [s["type"] for s in by_id[c]["shared"]] == ["url"]
    assert by_id[c]["shared"][0]["display"] == "kyc.example.co.in/verify"
    # observed before inferred
    assert [i["complaint"]["id"] for i in out["items"]] == [c, b]


async def test_bank_account_alone_links_and_ifsc_is_compared(client, inv, mk):
    a = await mk("a", bank_account="001234567890", ifsc="HDFC0001234")
    b = await mk("b", bank_account="0012 3456 7890", ifsc="HDFC0001234")
    c = await mk("c", bank_account="001234567890", ifsc="ICIC0004321")
    d = await mk("d", bank_account="001234567890")
    out = await related(client, inv, a)
    ifsc = {i["complaint"]["id"]: i["shared"][0]["ifsc"] for i in out["items"]}
    assert ifsc == {b: "same", c: "different", d: "not_given"}
    assert {i["shared"][0]["display"] for i in out["items"]} == {"••••••7890"}


async def test_social_account_needs_the_same_platform(client, inv, mk):
    a = await mk("a", social_platform="Instagram", social_account="@scam.desk")
    b = await mk("b", social_platform="insta", social_account="Scam.Desk")
    await mk("c", social_platform="Telegram", social_account="scam.desk")
    out = await related(client, inv, a)
    assert [i["complaint"]["id"] for i in out["items"]] == [b]
    assert out["items"][0]["shared"][0]["display"] == "instagram: sc•••"


async def test_related_pagination(client, inv, mk):
    a = await mk("a", upi_id="mule.one@ybl")
    others = [await mk(f"p{i}", upi_id="mule.one@ybl") for i in range(5)]
    p1 = await related(client, inv, a, limit=2, offset=0)
    p3 = await related(client, inv, a, limit=2, offset=4)
    assert p1["total"] == 5 and len(p1["items"]) == 2 and len(p3["items"]) == 1
    seen = {i["complaint"]["id"] for i in p1["items"]} | {i["complaint"]["id"] for i in p3["items"]}
    assert seen <= set(others)
    r = await client.get(f"/complaints/{a}/related", headers=inv, params={"limit": 51})
    assert r.status_code == 422


# ------------------------------------------------------------------------------ reviews
async def test_confirm_then_reject_latest_wins_history_kept_and_audited(
    client, inv, admin, mk, app
):
    a = await mk("a", upi_id="mule.one@ybl")
    b = await mk("b", upi_id="mule.one@ybl")
    r = await review(client, inv, b, a, "confirmed", note="Same account in both statements.")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["link_type"] == "confirmed" and body["previous_decision"] is None
    assert body["review"]["basis"] == "observed"
    out = await related(client, inv, a)
    assert out["items"][0]["link_type"] == "confirmed"
    assert out["items"][0]["review"]["note"] == "Same account in both statements."
    assert out["items"][0]["review"]["review_count"] == 1

    r = await review(client, admin, a, b, "rejected")
    assert r.status_code == 201 and r.json()["previous_decision"] == "confirmed"
    out = await related(client, inv, b)
    item = out["items"][0]
    assert item["link_type"] == "rejected" and item["basis"] == "observed"
    assert item["review"]["review_count"] == 2 and item["review"]["note"] is None

    with app.state.store.engine.connect() as c:
        rows = c.execute(sa.text("SELECT decision, seq FROM link_reviews ORDER BY seq")).all()
        audit = c.execute(
            sa.text(
                "SELECT action, before, after FROM admin_audit WHERE action LIKE 'link_%' "
                "ORDER BY created_at"
            )
        ).all()
    assert [tuple(x) for x in rows] == [("confirmed", 1), ("rejected", 2)]
    assert [x.action for x in audit] == ["link_confirm", "link_reject"]
    blob = json.dumps([[x.before, x.after] for x in audit], default=str).casefold()
    assert "mule" not in blob and "ybl" not in blob and "statements" not in blob
    low, high = sorted((a, b))
    after = audit[0].after if isinstance(audit[0].after, dict) else json.loads(audit[0].after)
    assert after["complaint_low"] == low and after["complaint_high"] == high


async def test_inferred_pairs_can_be_confirmed(client, inv, mk):
    a = await mk("a", suspicious_url="https://kyc.example.com/a")
    b = await mk("b", suspicious_url="https://pay.example.com/b")
    r = await review(client, inv, a, b, "confirmed")
    assert r.status_code == 201 and r.json()["review"]["basis"] == "inferred"
    item = (await related(client, inv, a))["items"][0]
    assert item["link_type"] == "confirmed" and item["basis"] == "inferred"


async def test_invalid_review_requests(client, inv, tok, mk):
    a = await mk("a", upi_id="mule.one@ybl")
    unrelated = await mk("b", upi_id="other.one@ybl")
    gone = await mk("c", upi_id="mule.one@ybl")
    hidden = await mk("d", upi_id="mule.one@ybl")
    r = await review(client, inv, a, a, "confirmed")
    assert r.status_code == 422
    r = await review(client, inv, a, unrelated, "confirmed")
    assert r.status_code == 409 and r.json()["error"] == "link_no_relation"
    r = await review(client, inv, a, "does-not-exist", "confirmed")
    assert r.status_code == 404
    assert (
        await client.delete(f"/complaints/{gone}", headers=citizen(tok, "c"))
    ).status_code == 204
    r = await review(client, inv, a, gone, "rejected")
    assert r.status_code == 409 and r.json()["error"] == "link_complaint_deleted"
    r = await client.patch(
        "/me", json={"privacy": {"allow_case_matching": False}}, headers=citizen(tok, "d")
    )
    assert r.status_code == 200
    r = await review(client, inv, a, hidden, "confirmed")
    assert r.status_code == 409 and r.json()["error"] == "link_matching_disabled"
    r = await review(client, inv, a, unrelated, "maybe")
    assert r.status_code == 422
    r = await review(client, inv, a, unrelated, "confirmed", note="x" * 501)
    assert r.status_code == 422


# ------------------------------------------------------------------------------ privacy
async def test_opt_out_is_excluded_everywhere_and_toggling_is_immediate(client, inv, tok, mk):
    a = await mk("a", upi_id="mule.one@ybl")
    b = await mk("b", upi_id="mule.one@ybl", suspect_phone="9876543210")
    hb = citizen(tok, "b")

    async def visible() -> dict[str, bool]:
        rel = await related(client, inv, a)
        g = (await client.get(f"/complaints/{a}/graph", headers=inv)).json()
        cl = (await client.get("/graph/clusters", headers=inv)).json()
        s = (
            await client.post(
                "/entities/search", json={"type": "upi", "value": "mule.one@ybl"}, headers=inv
            )
        ).json()
        return {
            "related": any(i["complaint"]["id"] == b for i in rel["items"]),
            "graph": any(n.get("complaint_id") == b for n in g["nodes"]),
            "clusters": any(b in c["member_ids"] for c in cl["items"]),
            "search": any(i["id"] == b for i in s["items"]),
        }

    assert all((await visible()).values())
    r = await client.patch("/me", json={"privacy": {"allow_case_matching": False}}, headers=hb)
    assert r.status_code == 200
    assert not any((await visible()).values())
    own = await related(client, inv, b)
    assert own["linking"] == "disabled" and own["items"] == [] and own["total"] == 0
    assert "complainant" in own["linking_message"]
    assert "mule" not in json.dumps(own).casefold()
    g = (await client.get(f"/complaints/{b}/graph", headers=inv)).json()
    assert g["linking"] == "disabled" and g["edges"] == []
    assert [n["complaint_id"] for n in g["nodes"]] == [b]
    # the complaint itself (with its own fields) is still available to the investigator
    d = await client.get(f"/complaints/{b}", headers=inv)
    assert d.status_code == 200 and d.json()["upi_id"] == "mule.one@ybl"
    # back on: effective immediately, nothing was deleted
    r = await client.patch("/me", json={"privacy": {"allow_case_matching": True}}, headers=hb)
    assert r.status_code == 200
    assert all((await visible()).values())


async def test_soft_deleted_complaints_are_excluded(client, inv, tok, mk):
    a = await mk("a", upi_id="mule.one@ybl")
    b = await mk("b", upi_id="mule.one@ybl")
    assert (await client.delete(f"/complaints/{b}", headers=citizen(tok, "b"))).status_code == 204
    assert (await related(client, inv, a))["items"] == []
    g = (await client.get(f"/complaints/{a}/graph", headers=inv)).json()
    assert [n["complaint_id"] for n in g["nodes"] if n["kind"] == "complaint"] == [a]
    assert (await client.get("/graph/clusters", headers=inv)).json()["items"] == []
    s = await client.post(
        "/entities/search", json={"type": "upi", "value": "mule.one@ybl"}, headers=inv
    )
    assert [i["id"] for i in s.json()["items"]] == [a]
    assert (await client.get(f"/complaints/{b}/related", headers=inv)).status_code == 404


# ------------------------------------------------------------------------------ graph
async def test_graph_depth_one_and_two(client, inv, mk):
    a = await mk("a", upi_id="mule.one@ybl")
    b = await mk("b", upi_id="mule.one@ybl", suspect_phone="9876543210")
    c = await mk("c", suspect_phone="+91 98765 43210")
    g1 = (await client.get(f"/complaints/{a}/graph", headers=inv)).json()
    ids1 = {n["complaint_id"] for n in g1["nodes"] if n["kind"] == "complaint"}
    assert ids1 == {a, b} and g1["depth"] == 1 and g1["truncated"] is False
    assert g1["disclaimer"] == DISCLAIMER
    ents = [n for n in g1["nodes"] if n["kind"] == "entity"]
    assert [(e["entity_type"], e["display"]) for e in ents] == [("upi", "mu•••@ybl")]
    center = next(n for n in g1["nodes"] if n.get("complaint_id") == a)
    assert center["center"] is True and center["distance"] == 0
    edges = g1["edges"]
    assert len(edges) == 2 and {e["kind"] for e in edges} == {"identifier"}
    assert all(e["link"] == "observed" and e["entity_type"] == "upi" for e in edges)
    assert all(e["source_field"] == "upi_id" and e["evidence"] for e in edges)
    assert g1["counts"] == {"complaints": 2, "entities": 1, "edges": 2}

    g2 = (await client.get(f"/complaints/{a}/graph", headers=inv, params={"depth": 2})).json()
    ids2 = {n["complaint_id"]: n["distance"] for n in g2["nodes"] if n["kind"] == "complaint"}
    assert ids2 == {a: 0, b: 1, c: 2}
    types = sorted(n["entity_type"] for n in g2["nodes"] if n["kind"] == "entity")
    assert types == ["phone", "upi"]
    assert "9876543210" not in json.dumps(g2) and "mule.one" not in json.dumps(g2)
    r = await client.get(f"/complaints/{a}/graph", headers=inv, params={"depth": 3})
    assert r.status_code == 422


async def test_graph_inferred_domain_edges_and_review_edges(client, inv, mk):
    a = await mk("a", suspicious_url="https://kyc.example.com/a")
    b = await mk("b", suspicious_url="https://pay.example.com/b")
    assert (await review(client, inv, a, b, "rejected", note="different sites")).status_code == 201
    g = (await client.get(f"/complaints/{a}/graph", headers=inv)).json()
    dom = [e for e in g["edges"] if e.get("entity_type") == "domain"]
    assert len(dom) == 2 and {e["link"] for e in dom} == {"inferred"}
    assert "inferred" in dom[0]["evidence"]
    rev = [e for e in g["edges"] if e["kind"] == "review"]
    assert (
        len(rev) == 1 and rev[0]["decision"] == "rejected" and rev[0]["note"] == "different sites"
    )
    # the centre's own (unshared) URL is shown; the other's URL is not shared, so not shown
    urls = [n for n in g["nodes"] if n.get("entity_type") == "url"]
    assert [u["display"] for u in urls] == ["kyc.example.com/a"]


async def test_graph_caps_set_the_truncated_flag(client, inv, mk, monkeypatch):
    monkeypatch.setattr(network_store, "MAX_GRAPH_COMPLAINTS", 3)
    a = await mk("a", upi_id="mule.one@ybl")
    for i in range(4):
        await mk(f"t{i}", upi_id="mule.one@ybl")
    g = (await client.get(f"/complaints/{a}/graph", headers=inv)).json()
    assert g["truncated"] is True
    assert g["counts"]["complaints"] == 3
    assert g["limits"]["complaints"] == 3


async def test_related_scan_cap_sets_truncated(client, inv, mk, monkeypatch):
    monkeypatch.setattr(network_store, "MAX_RELATED_ROWS", 2)
    a = await mk("a", upi_id="mule.one@ybl")
    for i in range(3):
        await mk(f"r{i}", upi_id="mule.one@ybl")
    out = await related(client, inv, a)
    assert out["truncated"] is True and out["total"] == 2


# ------------------------------------------------------------------------------ clusters
async def test_clusters_on_a_small_synthetic_set(client, inv, mk):
    # TEST DATA: {A, B} share a UPI id; {C, D, E} are chained by a phone and an account;
    # F has nothing in common; G shares only a domain with A (inferred: never clusters)
    a = await mk("A", upi_id="mule.one@ybl", suspicious_url="https://kyc.example.com/a")
    b = await mk("B", upi_id="MULE.ONE@ybl")
    c = await mk("C", suspect_phone="9876500001")
    d = await mk("D", suspect_phone="09876500001", bank_account="5550001111")
    e = await mk("E", bank_account="5550001111")
    await mk("F", upi_id="lonely@ybl")
    await mk("G", suspicious_url="https://other.example.com/g")
    out = (await client.get("/graph/clusters", headers=inv)).json()
    assert out["total"] == 2 and out["truncated"] is False
    big, small = out["items"]
    assert big["size"] == 3 and set(big["member_ids"]) == {c, d, e}
    assert big["connecting_types"] == {"bank_account": 1, "phone": 1}
    assert small["size"] == 2 and set(small["member_ids"]) == {a, b}
    assert small["connecting_types"] == {"upi": 1}
    assert {m["id"] for m in big["members"]} == {c, d, e}
    assert big["id"].startswith("cl-") and out["disclaimer"] == DISCLAIMER
    page = (
        await client.get("/graph/clusters", headers=inv, params={"limit": 1, "offset": 1})
    ).json()
    assert [x["id"] for x in page["items"]] == [small["id"]]


async def test_clusters_sorted_by_size_then_recency(client, inv, mk):
    a1 = await mk("a1", upi_id="first@ybl")
    await mk("a2", upi_id="first@ybl")
    await mk("b1", upi_id="second@ybl")
    b2 = await mk("b2", upi_id="second@ybl")
    out = (await client.get("/graph/clusters", headers=inv)).json()
    assert [b2 in x["member_ids"] for x in out["items"]] == [True, False]
    assert a1 in out["items"][1]["member_ids"]


# ------------------------------------------------------------------------------ search
async def test_identifier_search_is_post_only_and_normalises(client, inv, mk):
    a = await mk("a", suspect_phone="9876543210")
    b = await mk("b", suspect_phone="+91 98765-43210")
    r = await client.post(
        "/entities/search", json={"type": "phone", "value": "098765 43210"}, headers=inv
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["entity"] == {"type": "phone", "display": "••••••3210"}
    assert {i["id"] for i in out["items"]} == {a, b} and out["total"] == 2
    assert all(i["source_field"] == "suspect_phone" for i in out["items"])
    assert "9876543210" not in r.text and out["disclaimer"] == DISCLAIMER
    assert (await client.get("/entities/search", headers=inv)).status_code == 405
    r = await client.post(
        "/entities/search",
        json={"type": "social_account", "value": "@who", "platform": "Instagram"},
        headers=inv,
    )
    assert r.status_code == 200 and r.json()["items"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"type": "phone", "value": "12"},
        {"type": "upi", "value": "secret-not-upi"},
        {"type": "social_account", "value": "@who"},
        {"type": "ssn", "value": "123"},
        {"type": "upi"},
    ],
)
async def test_identifier_search_rejects_invalid_values_without_echo(client, inv, body):
    r = await client.post("/entities/search", json=body, headers=inv)
    assert r.status_code == 422
    assert "secret-not-upi" not in r.text and '"12"' not in r.text


# ------------------------------------------------------------------------------ logs, health
async def test_identifiers_never_reach_the_logs(client, inv, mk, caplog):
    caplog.set_level(logging.DEBUG)
    a = await mk("a", upi_id="unique.mule@ybl", suspect_phone="9811122233")
    b = await mk("b", upi_id="unique.mule@ybl", bank_account="7778889990")
    await client.get(f"/complaints/{a}/related", headers=inv)
    await client.get(f"/complaints/{a}/graph", headers=inv, params={"depth": 2})
    await client.get("/graph/clusters", headers=inv)
    await client.post(
        "/entities/search", json={"type": "upi", "value": "unique.mule@ybl"}, headers=inv
    )
    await client.post("/entities/search", json={"type": "upi", "value": "bad value"}, headers=inv)
    await review(client, inv, a, b, "confirmed", note="note with 7778889990")
    text = "\n".join(r.getMessage() for r in caplog.records).casefold()
    for secret in ("unique.mule", "9811122233", "7778889990", "98111", "note with"):
        assert secret not in text
    assert "/entities/search" in text  # the access log names the route, never the body


async def test_admin_health_reports_the_sql_graph_backend(client, admin):
    h = (await client.get("/admin/health", headers=admin)).json()
    assert h["graph"]["status"] == "sql"
    assert h["graph"]["message"] == "sql (neo4j not configured)"
    assert h["neo4j"]["status"] == "not_configured"


async def test_session_capabilities_include_network_for_staff(client, inv, tok):
    s = (await client.get("/auth/session", headers=inv)).json()
    assert {"network:read", "network:review"} <= set(s["capabilities"])
    s = (await client.get("/auth/session", headers=tok("a"))).json()
    assert not any(c.startswith("network:") for c in s["capabilities"])


# --------------------------------------------------------------------- indirect privacy leaks
async def test_opted_out_complaint_cannot_bridge_or_be_counted(client, inv, tok, mk):
    # review 2026-10-18: A -(UPI)- B -(phone)- C. When B opts out, nothing may pass through B:
    # C must not be reached from A, A and C must not form a cluster through B, counts must
    # not include B, and an existing review involving B must not show from A's side.
    a = await mk("a", upi_id="bridge.upi@ybl")
    b = await mk("b", upi_id="bridge.upi@ybl", suspect_phone="9811122233")
    c = await mk("c", suspect_phone="9811122233")
    r = await review(client, inv, a, b, "confirmed", note="checked")
    assert r.status_code == 201
    g2 = (await client.get(f"/complaints/{a}/graph", params={"depth": 2}, headers=inv)).json()
    assert {n.get("complaint_id") for n in g2["nodes"]} >= {a, b, c}  # visible while b opts in

    patch = await client.patch(
        "/me", json={"privacy": {"allow_case_matching": False}}, headers=citizen(tok, "b")
    )
    assert patch.status_code == 200
    g2 = (await client.get(f"/complaints/{a}/graph", params={"depth": 2}, headers=inv)).json()
    ids = {n.get("complaint_id") for n in g2["nodes"] if n["kind"] == "complaint"}
    assert ids == {a}  # neither b nor c (reachable only through b)
    assert all(n.get("complaint_count", 0) <= 1 for n in g2["nodes"] if n["kind"] == "entity")
    assert not [e for e in g2["edges"] if e["kind"] == "review"]
    cl = (await client.get("/graph/clusters", headers=inv)).json()
    assert all(not ({a, c} <= set(x["member_ids"])) for x in cl["items"])
    assert all(b not in x["member_ids"] for x in cl["items"])
    s = (await client.post("/entities/search", json={"type": "upi", "value": "bridge.upi@ybl"},
                           headers=inv)).json()  # fmt: skip
    assert s["total"] == 1 and [i["id"] for i in s["items"]] == [a]
    rel = await related(client, inv, a)
    assert rel["items"] == [] and rel["total"] == 0
    blob = json.dumps(rel) + json.dumps(g2) + json.dumps(cl) + json.dumps(s)
    assert b not in blob and c not in blob
    # a new review of the pair is refused while b opts out
    r = await review(client, inv, a, b, "rejected")
    assert r.status_code == 409 and r.json()["error"] == "link_matching_disabled"
