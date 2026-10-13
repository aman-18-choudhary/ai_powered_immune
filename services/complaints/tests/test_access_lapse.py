"""Investigator / bootstrap-admin access is re-evaluated on every request against the CURRENT
verified token email and the CURRENT env lists. Admin-granted roles are unaffected."""

import dataclasses

import httpx
from conftest import ADMIN_EMAIL, INVESTIGATOR_EMAIL, bearer, create
from sqlalchemy import text


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def variant(settings, **kw):
    return dataclasses.replace(settings, **kw)


async def audit_actions(c, admin_headers):
    r = await c.get("/admin/audit?limit=100", headers=admin_headers)
    return [a["action"] for a in r.json()["items"]]


async def test_allowlist_removal_lapses_next_request_audited_once(
    make_app, settings, client, tok, admin
):
    h = tok("inv")
    r = await client.post("/auth/entry", json={"entry": "investigator"}, headers=h)
    assert r.json()["user"]["role"] == "INVESTIGATOR"
    owner = await create(client, tok("a"))

    app2 = make_app(settings=variant(settings, investigator_allowlist=frozenset()))
    async with client_for(app2) as c2:
        for _ in range(3):  # repeated requests: lapse is audited once
            s = (await c2.get("/auth/session", headers=h)).json()
            assert s["user"]["role"] == "CITIZEN" and s["investigator_access"] == "lapsed"
            assert "citizen" in s["message"].casefold()
        assert (await c2.get("/admin/users", headers=h)).status_code == 403
        assert (await c2.get(f"/complaints/{owner['id']}", headers=h)).status_code == 404
        assert (await c2.get("/complaints", headers=h)).json()["total"] == 0
        r = await c2.patch(
            f"/complaints/{owner['id']}/status", json={"status": "UNDER_REVIEW"}, headers=h
        )
        assert r.status_code == 403
        users = (await c2.get("/admin/users", headers=admin)).json()["items"]
        row = next(u for u in users if u["email"] == INVESTIGATOR_EMAIL.casefold())
        assert (
            row["role"] == "INVESTIGATOR" and row["role_source"] == "allowlist"
        )  # stored untouched
        acts = await audit_actions(c2, admin)
        assert acts.count("investigator_access_lapsed") == 1

    # re-adding the email restores access (original settings), audited as restored once
    r = await client.get("/auth/session", headers=h)
    assert r.json()["user"]["role"] == "INVESTIGATOR" and "investigator_access" not in r.json()
    assert (await client.get(f"/complaints/{owner['id']}", headers=h)).status_code == 200
    await client.get("/auth/session", headers=h)
    acts = await audit_actions(client, admin)
    assert acts.count("investigator_access_restored") == 1
    # a second lapse is audited again
    app3 = make_app(settings=variant(settings, investigator_allowlist=frozenset()))
    async with client_for(app3) as c3:
        await c3.get("/auth/session", headers=h)
        assert (await audit_actions(c3, admin)).count("investigator_access_lapsed") == 2


async def test_unverified_email_lapses(client, tok, inv):
    r = await client.get("/auth/session", headers=tok("inv", verified=False))
    assert r.json()["user"]["role"] == "CITIZEN" and r.json()["investigator_access"] == "lapsed"
    assert (await client.get("/admin/users", headers=tok("inv", verified=False))).status_code == 403


async def test_email_change_lapses_and_case_whitespace_is_tolerated(client, tok, inv):
    spaced = tok("inv", email="  " + INVESTIGATOR_EMAIL.upper() + " ")
    assert (await client.get("/auth/session", headers=spaced)).json()["user"]["role"] == (
        "INVESTIGATOR"
    )
    moved = tok("inv", email="someone.else@example.org")
    r = await client.get("/auth/session", headers=moved)
    assert r.json()["user"]["role"] == "CITIZEN" and r.json()["investigator_access"] == "lapsed"


async def test_admin_granted_investigator_unaffected(make_app, settings, client, tok, admin):
    uid = (await client.get("/auth/session", headers=tok("a"))).json()["user"]["id"]
    r = await client.put(
        f"/admin/users/{uid}/role",
        json={"role": "INVESTIGATOR", "expected_role": "CITIZEN"},
        headers=admin,
    )
    assert r.status_code == 200 and r.json()["role_source"] == "admin"
    app2 = make_app(settings=variant(settings, investigator_allowlist=frozenset()))
    async with client_for(app2) as c2:
        s = (await c2.get("/auth/session", headers=tok("a"))).json()
        assert s["user"]["role"] == "INVESTIGATOR" and "investigator_access" not in s


async def test_admin_can_pin_an_allowlist_grant(make_app, settings, client, tok, inv, admin):
    users = (await client.get("/admin/users", headers=admin)).json()["items"]
    uid = next(u for u in users if u["role"] == "INVESTIGATOR")["id"]
    r = await client.put(
        f"/admin/users/{uid}/role",
        json={"role": "INVESTIGATOR", "expected_role": "INVESTIGATOR"},
        headers=admin,
    )
    assert r.status_code == 200 and r.json()["role_source"] == "admin"
    app2 = make_app(settings=variant(settings, investigator_allowlist=frozenset()))
    async with client_for(app2) as c2:
        assert (await c2.get("/auth/session", headers=tok("inv"))).json()["user"]["role"] == (
            "INVESTIGATOR"
        )


async def test_bootstrap_admin_lapses_when_another_admin_exists(
    make_app, settings, client, tok, admin
):
    h2 = tok("admin2")
    uid2 = (await client.get("/auth/session", headers=h2)).json()["user"]["id"]
    r = await client.put(
        f"/admin/users/{uid2}/role",
        json={"role": "ADMIN", "expected_role": "CITIZEN"},
        headers=admin,
    )
    assert r.status_code == 200
    app2 = make_app(settings=variant(settings, admin_bootstrap_emails=frozenset()))
    async with client_for(app2) as c2:
        s = (await c2.get("/auth/session", headers=admin)).json()
        assert s["user"]["role"] == "CITIZEN" and s["admin_access"] == "lapsed"
        assert (await c2.get("/admin/users", headers=admin)).status_code == 403
        assert (await c2.get("/auth/session", headers=h2)).json()["user"]["role"] == "ADMIN"
        acts = await audit_actions(c2, h2)
        assert acts.count("admin_access_lapsed") == 1
    # env restored: access restored
    assert (await client.get("/auth/session", headers=admin)).json()["user"]["role"] == "ADMIN"


async def test_only_admin_is_never_locked_out(make_app, settings, client, admin):
    app2 = make_app(settings=variant(settings, admin_bootstrap_emails=frozenset()))
    async with client_for(app2) as c2:
        for _ in range(2):
            s = (await c2.get("/auth/session", headers=admin)).json()
            assert s["user"]["role"] == "ADMIN" and s["admin_access"] == "lapsed_retained"
        assert (await c2.get("/admin/users", headers=admin)).status_code == 200
        acts = await audit_actions(c2, admin)
        assert acts.count("last_admin_bootstrap_lapsed") == 1


async def test_legacy_null_role_source_is_derived(make_app, settings, client, tok, inv):
    app_store = client._transport.app.state.store  # noqa: SLF001
    with app_store.engine.begin() as conn:
        conn.execute(text("UPDATE users SET role_source = NULL"))
    assert (await client.get("/auth/session", headers=tok("inv"))).json()["user"]["role"] == (
        "INVESTIGATOR"
    )
    app2 = make_app(settings=variant(settings, investigator_allowlist=frozenset()))
    async with client_for(app2) as c2:
        s = (await c2.get("/auth/session", headers=tok("inv"))).json()
        assert s["user"]["role"] == "CITIZEN" and s["investigator_access"] == "lapsed"


async def test_role_not_cached_between_requests(client, tok, inv):
    assert (await client.get("/auth/session", headers=tok("inv"))).json()["user"]["role"] == (
        "INVESTIGATOR"
    )
    assert (await client.get("/auth/session", headers=tok("inv", verified=False))).json()["user"][
        "role"
    ] == "CITIZEN"
    assert (await client.get("/auth/session", headers=tok("inv"))).json()["user"]["role"] == (
        "INVESTIGATOR"
    )
    assert bearer and ADMIN_EMAIL
