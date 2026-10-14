"""GET/PATCH /me: profile and privacy settings. The role can never change through this route."""

import pytest


async def test_get_me_returns_profile_and_default_privacy(client, tok):
    r = await client.get("/me", headers=tok("a"))
    assert r.status_code == 200, r.text
    me = r.json()
    assert set(me) >= {"id", "email", "email_verified", "display_name", "role", "created_at",
                       "privacy"}  # fmt: skip
    assert me["email"] == "citizen.a@example.org" and me["email_verified"] is True
    assert me["display_name"] == "Test User" and me["role"] == "CITIZEN"
    assert me["created_at"]
    assert me["privacy"] == {"allow_case_matching": True}


async def test_get_me_requires_auth(client):
    assert (await client.get("/me")).status_code == 401
    assert (await client.patch("/me", json={"display_name": "x"})).status_code == 401


async def test_me_role_is_the_effective_role(client, tok, inv, admin):
    assert (await client.get("/me", headers=inv)).json()["role"] == "INVESTIGATOR"
    assert (await client.get("/me", headers=admin)).json()["role"] == "ADMIN"


async def test_patch_display_name_persists_across_sign_ins(client, tok):
    r = await client.patch("/me", json={"display_name": "  Asha K  "}, headers=tok("a"))
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Asha K"
    # the token still carries name "Test User": a user-chosen name is not overwritten
    again = (await client.get("/me", headers=tok("a"))).json()
    assert again["display_name"] == "Asha K"


async def test_patch_display_name_null_returns_to_the_sign_in_name(client, tok):
    await client.patch("/me", json={"display_name": "Custom"}, headers=tok("a"))
    r = await client.patch("/me", json={"display_name": None}, headers=tok("a"))
    assert r.status_code == 200 and r.json()["display_name"] == "Test User"
    assert (await client.get("/me", headers=tok("a"))).json()["display_name"] == "Test User"


async def test_patch_privacy_setting(client, tok):
    r = await client.patch("/me", json={"privacy": {"allow_case_matching": False}},
                           headers=tok("a"))  # fmt: skip
    assert r.status_code == 200, r.text
    assert r.json()["privacy"] == {"allow_case_matching": False}
    assert (await client.get("/me", headers=tok("a"))).json()["privacy"][
        "allow_case_matching"
    ] is False
    # another user is unaffected
    assert (await client.get("/me", headers=tok("b"))).json()["privacy"][
        "allow_case_matching"
    ] is True


async def test_empty_patch_changes_nothing(client, tok):
    before = (await client.get("/me", headers=tok("a"))).json()
    r = await client.patch("/me", json={}, headers=tok("a"))
    assert r.status_code == 200 and r.json() == before


@pytest.mark.parametrize(
    "body",
    [
        {"role": "ADMIN"},
        {"display_name": "Eve", "role": "ADMIN"},
        {"email": "root@example.org"},
        {"email_verified": True},
        {"role_source": "admin"},
        {"is_active": True},
        {"privacy": {"allow_case_matching": False, "role": "ADMIN"}},
    ],
)
async def test_patch_cannot_elevate_or_change_identity(client, tok, body):
    h = tok("a")
    before = (await client.get("/me", headers=h)).json()
    r = await client.patch("/me", json=body, headers=h)
    assert r.status_code == 422, r.text
    assert "role" not in str(r.json()["detail"])  # unknown key names are not echoed
    after = (await client.get("/me", headers=h)).json()
    assert after == before and after["role"] == "CITIZEN"
    s = (await client.get("/auth/session", headers=h)).json()
    assert s["user"]["role"] == "CITIZEN" and "admin:users" not in s["capabilities"]


@pytest.mark.parametrize(
    "body",
    [
        {"display_name": "x" * 101},
        {"display_name": "bad\nname"},
        {"display_name": 5},
        {"privacy": {"allow_case_matching": "maybe"}},
        {"privacy": None},
    ],
)
async def test_patch_validation_422_without_echo(client, tok, body):
    r = await client.patch("/me", json=body, headers=tok("a"))
    assert r.status_code == 422, r.text
    d = r.json()["detail"][0]
    assert "input" not in d and "ctx" not in d and "maybe" not in r.text


async def test_patch_is_audited_without_the_name(client, tok, admin):
    await client.patch("/me", json={"display_name": "Secret Name",
                                    "privacy": {"allow_case_matching": False}},
                       headers=tok("a"))  # fmt: skip
    audit = (await client.get("/admin/audit", params={"limit": 100}, headers=admin)).json()
    rows = [a for a in audit["items"] if a["action"] == "profile_update"]
    assert len(rows) == 1
    assert "Secret Name" not in str(rows[0])
    assert rows[0]["after"]["allow_case_matching"] is False
    assert "display_name" in rows[0]["after"]["changed"]
