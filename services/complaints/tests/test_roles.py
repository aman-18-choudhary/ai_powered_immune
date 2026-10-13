"""Entry flow, investigator allowlist, admin bootstrap, role administration, audit."""

import dataclasses

import httpx
import pytest
from conftest import ADMIN_EMAIL, INVESTIGATOR_EMAIL, bearer

DENIED = {
    "error": "investigator_not_approved",
    "message": "Investigator access is limited to approved accounts. You can continue as a citizen.",
}


async def test_allowlisted_verified_email_becomes_investigator(client, tok):
    r = await client.post("/auth/entry", json={"entry": "investigator"}, headers=tok("inv"))
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "INVESTIGATOR"
    s = await client.get("/auth/session", headers=tok("inv"))
    assert s.json()["user"]["role"] == "INVESTIGATOR"


async def test_non_allowlisted_gets_friendly_403_without_leak(client, tok):
    r = await client.post("/auth/entry", json={"entry": "investigator"}, headers=tok("a"))
    assert r.status_code == 403 and r.json() == DENIED
    text = r.text.casefold()
    assert "example.org" not in text and "allowlist" not in text
    s = await client.get("/auth/session", headers=tok("a"))
    assert s.json()["user"]["role"] == "CITIZEN"


async def test_unverified_allowlisted_email_denied(client, tok):
    r = await client.post(
        "/auth/entry", json={"entry": "investigator"}, headers=tok("inv", verified=False)
    )
    assert r.status_code == 403 and r.json() == DENIED


async def test_missing_email_claims_denied(client, tok):
    r = await client.post(
        "/auth/entry",
        json={"entry": "investigator"},
        headers=tok("inv", email=None, verified=None),
    )
    assert r.status_code == 403 and r.json() == DENIED


async def test_allowlist_casing_and_whitespace(client, idp, settings, make_app):
    s = dataclasses.replace(
        settings, investigator_allowlist=frozenset({"  Spacey.One@EXAMPLE.org ".strip().casefold()})
    )
    app = make_app(settings=s)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        h = bearer(idp.token(sub="sp", email="SPACEY.one@example.ORG"))
        r = await c.post("/auth/entry", json={"entry": "investigator"}, headers=h)
        assert r.status_code == 200


def test_allowlist_env_parsing_is_tolerant(monkeypatch, tmp_path):
    from complaints.config import Settings

    monkeypatch.setenv("COMPLAINTS_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.test.example")
    monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.test.example/jwks.json")
    monkeypatch.setenv("COMPLAINTS_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setenv("INVESTIGATOR_ALLOWLIST", " A@x.org ,B@X.org;\n c@x.org ,, ")
    s = Settings.from_env()
    assert s.investigator_allowlist == frozenset({"a@x.org", "b@x.org", "c@x.org"})


async def test_citizen_entry_never_elevates_even_for_allowlisted(client, tok):
    r = await client.post("/auth/entry", json={"entry": "citizen"}, headers=tok("inv"))
    assert r.status_code == 200 and r.json()["user"]["role"] == "CITIZEN"
    s = await client.get("/auth/session", headers=tok("inv"))
    assert s.json()["user"]["role"] == "CITIZEN"


async def test_role_in_body_and_claims_ignored(client, tok):
    r = await client.post(
        "/auth/entry", json={"entry": "citizen", "role": "ADMIN"}, headers=tok("a", role="admin")
    )
    assert r.status_code == 200 and r.json()["user"]["role"] == "CITIZEN"
    r = await client.get(
        "/auth/session", headers=tok("a", role="ADMIN", roles=["ADMIN"], public_metadata={"role": "ADMIN"})
    )
    assert r.json()["user"]["role"] == "CITIZEN"
    r = await client.post("/auth/entry", json={"entry": "root"}, headers=tok("a"))
    assert r.status_code == 422


async def test_citizen_cannot_reach_staff_endpoints(client, tok):
    h = tok("a")
    for method, path in [
        ("GET", "/admin/users"), ("GET", "/admin/audit"), ("GET", "/admin/health"),
        ("PUT", "/admin/users/x/role"), ("PUT", "/admin/users/x/active"),
    ]:  # fmt: skip
        r = await client.request(method, path, headers=h, json={"role": "ADMIN", "expected_role": "CITIZEN"})
        assert r.status_code == 403, (method, path, r.status_code)


async def test_admin_bootstrap_only_for_verified_env_email(client, tok):
    r = await client.get("/auth/session", headers=tok("admin"))
    assert r.json()["user"]["role"] == "ADMIN"
    # same email, unverified: never admin
    r = await client.get("/auth/session", headers=tok("admin", sub="evil", verified=False))
    assert r.json()["user"]["role"] == "CITIZEN"
    # claimed admin email but not in env: no
    r = await client.get("/auth/session", headers=tok("a", email="someone@else.org"))
    assert r.json()["user"]["role"] == "CITIZEN"


async def test_bootstrap_audited_and_not_repeated_after_demotion(client, tok, admin):
    audit = (await client.get("/admin/audit", headers=admin)).json()["items"]
    assert [a["action"] for a in audit] == ["admin_bootstrap"]
    # a second admin appears so the first can be demoted
    h2 = tok("admin2")
    await client.get("/auth/session", headers=h2)
    users = (await client.get("/admin/users", headers=admin)).json()["items"]
    me = next(u for u in users if u["email"] == ADMIN_EMAIL)
    other = next(u for u in users if u["email"] == "second.admin@example.org")
    r = await client.put(
        f"/admin/users/{other['id']}/role",
        json={"role": "ADMIN", "expected_role": "CITIZEN"},
        headers=admin,
    )
    assert r.status_code == 200
    r = await client.put(
        f"/admin/users/{me['id']}/role",
        json={"role": "CITIZEN", "expected_role": "ADMIN"},
        headers=admin,
    )
    assert r.status_code == 200
    # deliberate demotion sticks across later logins (bootstrap applies once)
    s = await client.get("/auth/session", headers=tok("admin"))
    assert s.json()["user"]["role"] == "CITIZEN"


async def test_role_change_by_non_admin_forbidden(client, tok, inv):
    uid = (await client.get("/auth/session", headers=tok("a"))).json()["user"]["id"]
    for h in (tok("a"), inv):
        r = await client.put(
            f"/admin/users/{uid}/role", json={"role": "ADMIN", "expected_role": "CITIZEN"}, headers=h
        )
        assert r.status_code == 403


async def test_admin_changes_role_with_audit_before_after(client, tok, admin):
    uid = (await client.get("/auth/session", headers=tok("a"))).json()["user"]["id"]
    r = await client.put(
        f"/admin/users/{uid}/role",
        json={"role": "INVESTIGATOR", "expected_role": "CITIZEN"},
        headers=admin,
    )
    assert r.status_code == 200 and r.json()["role"] == "INVESTIGATOR"
    audit = (await client.get("/admin/audit", headers=admin)).json()["items"]
    row = audit[0]
    assert row["action"] == "role_change" and row["target_user_id"] == uid
    assert row["before"] == {"role": "CITIZEN"} and row["after"] == {"role": "INVESTIGATOR"}
    assert row["actor_user_id"]


async def test_role_change_validates_enum_and_expected_role(client, tok, admin):
    uid = (await client.get("/auth/session", headers=tok("a"))).json()["user"]["id"]
    bad = await client.put(
        f"/admin/users/{uid}/role", json={"role": "SUPERUSER", "expected_role": "CITIZEN"}, headers=admin
    )
    assert bad.status_code == 422 and "SUPERUSER" not in bad.text
    stale = await client.put(
        f"/admin/users/{uid}/role", json={"role": "INVESTIGATOR", "expected_role": "ADMIN"}, headers=admin
    )
    assert stale.status_code == 409 and stale.json()["error"] == "role_conflict"
    nf = await client.put(
        "/admin/users/00000000-0000-0000-0000-000000000000/role",
        json={"role": "INVESTIGATOR", "expected_role": "CITIZEN"},
        headers=admin,
    )
    assert nf.status_code == 404


async def test_granting_privileged_role_requires_verified_email(client, tok, admin):
    uid = (
        await client.get("/auth/session", headers=tok("a", sub="unv", verified=False))
    ).json()["user"]["id"]
    r = await client.put(
        f"/admin/users/{uid}/role", json={"role": "INVESTIGATOR", "expected_role": "CITIZEN"}, headers=admin
    )
    assert r.status_code == 409 and r.json()["error"] == "target_email_not_verified"


async def test_last_admin_protected(client, admin):
    users = (await client.get("/admin/users", headers=admin)).json()["items"]
    me = next(u for u in users if u["role"] == "ADMIN")
    r = await client.put(
        f"/admin/users/{me['id']}/role", json={"role": "CITIZEN", "expected_role": "ADMIN"}, headers=admin
    )
    assert r.status_code == 409 and r.json()["error"] == "last_admin"
    r = await client.put(f"/admin/users/{me['id']}/active", json={"is_active": False}, headers=admin)
    assert r.status_code == 409 and r.json()["error"] == "last_admin"
    still = await client.get("/auth/session", headers=admin)
    assert still.json()["user"]["role"] == "ADMIN"


async def test_inactive_user_blocked_everywhere(client, tok, admin):
    h = tok("a")
    uid = (await client.get("/auth/session", headers=h)).json()["user"]["id"]
    r = await client.put(f"/admin/users/{uid}/active", json={"is_active": False}, headers=admin)
    assert r.status_code == 200 and r.json()["is_active"] is False
    for method, path in [("GET", "/auth/session"), ("GET", "/complaints"), ("GET", "/alerts"),
                         ("GET", "/me/stats")]:  # fmt: skip
        resp = await client.request(method, path, headers=h)
        assert resp.status_code == 403 and resp.json()["error"] == "account_inactive", path
    r = await client.post("/auth/entry", json={"entry": "citizen"}, headers=h)
    assert r.status_code == 403
    # reactivation restores access
    await client.put(f"/admin/users/{uid}/active", json={"is_active": True}, headers=admin)
    assert (await client.get("/auth/session", headers=h)).status_code == 200
    actions = [a["action"] for a in (await client.get("/admin/audit", headers=admin)).json()["items"]]
    assert actions.count("deactivate") == 1 and actions.count("activate") == 1


async def test_investigator_entry_is_audited_and_not_repeated(client, tok, admin):
    h = tok("inv")
    for _ in range(2):
        r = await client.post("/auth/entry", json={"entry": "investigator"}, headers=h)
        assert r.status_code == 200
    actions = [a["action"] for a in (await client.get("/admin/audit", headers=admin)).json()["items"]]
    assert actions.count("investigator_entry") == 1


async def test_admin_users_paginated_and_bounded(client, tok, admin):
    for i in range(5):
        await client.get(
            "/auth/session", headers=tok("a", sub=f"u{i}", email=f"u{i}@example.org")
        )
    r = await client.get("/admin/users?limit=2", headers=admin)
    body = r.json()
    assert len(body["items"]) == 2 and body["total"] >= 6
    assert (await client.get("/admin/users?limit=1000", headers=admin)).status_code == 422
    assert (await client.get("/admin/users?limit=0", headers=admin)).status_code == 422
    assert all("password" not in u for u in body["items"])


async def test_investigator_cannot_use_admin_api(client, inv, tok):
    assert (await client.get("/admin/users", headers=inv)).status_code == 403
    assert INVESTIGATOR_EMAIL  # silence unused import


@pytest.mark.parametrize("path", ["/complaints", "/alerts", "/me/stats", "/admin/users"])
async def test_every_protected_endpoint_needs_a_token(client, path):
    assert (await client.get(path)).status_code == 401
