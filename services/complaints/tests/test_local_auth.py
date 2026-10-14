"""Local (development-only) sign-in: email + one-time code printed to the server terminal."""

import dataclasses
import logging
import time

import httpx
import jwt
import pytest
from conftest import INVESTIGATOR_EMAIL, bearer

from complaints.api import create_app
from complaints.local_auth import LocalAuth, local_subject, print_to_terminal

SECRET = "local-auth-test-secret-" + "s" * 32


class Clock:
    def __init__(self) -> None:
        self.t = time.time()

    def __call__(self) -> float:
        return self.t


class Outbox:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def __call__(self, email: str, code: str) -> None:
        self.sent.append((email, code))

    def code(self) -> str:
        return self.sent[-1][1]


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def outbox():
    return Outbox()


@pytest.fixture
def local_settings(settings):
    return dataclasses.replace(settings, auth_mode="local", demo_jwt_secret=SECRET)


@pytest.fixture
def lapp(local_settings, analysis, clock, outbox):
    app = create_app(local_settings, analysis=analysis,
                     local_auth=LocalAuth(SECRET, deliver=outbox, clock=clock))  # fmt: skip
    yield app
    app.state.store.close()


@pytest.fixture
def lc(lapp):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=lapp), base_url="http://t")


async def sign_in(lc, outbox, email="new.person@example.org", **kw) -> dict[str, str]:
    r = await lc.post("/local-auth/start", json={"email": email})
    assert r.status_code == 202, r.text
    r = await lc.post("/local-auth/verify", json={"email": email, "code": outbox.code(), **kw})
    assert r.status_code == 200, r.text
    return bearer(r.json()["token"])


async def test_happy_path_creates_a_verified_citizen(lc, outbox):
    r = await lc.post("/local-auth/start", json={"email": "  New.Person@Example.ORG "})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "code_sent" and body["delivery"] == "server_terminal"
    assert outbox.sent and outbox.sent[-1][0] == "new.person@example.org"
    code = outbox.code()
    assert len(code) == 6 and code.isdigit() and code not in r.text
    r = await lc.post("/local-auth/verify",
                      json={"email": "new.person@example.org", "code": code, "name": "Neha"})  # fmt: skip
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["token_type"] == "Bearer" and out["expires_in"] == 8 * 3600
    assert code not in r.text
    claims = jwt.decode(out["token"], SECRET, algorithms=["HS256"])
    assert claims["sub"] == local_subject("new.person@example.org")
    assert claims["email"] == "new.person@example.org" and claims["email_verified"] is True
    assert claims["exp"] - claims["iat"] == 8 * 3600 and "role" not in claims
    s = (await lc.get("/auth/session", headers=bearer(out["token"]))).json()
    assert s["user"]["role"] == "CITIZEN" and s["user"]["email_verified"] is True
    me = (await lc.get("/me", headers=bearer(out["token"]))).json()
    assert me["display_name"] == "Neha"


async def test_code_is_single_use(lc, outbox):
    await lc.post("/local-auth/start", json={"email": "a@example.org"})
    code = outbox.code()
    assert (
        await lc.post("/local-auth/verify", json={"email": "a@example.org", "code": code})
    ).status_code == 200
    r = await lc.post("/local-auth/verify", json={"email": "a@example.org", "code": code})
    assert r.status_code == 400 and r.json()["error"] == "invalid_code"


async def test_wrong_code(lc, outbox):
    await lc.post("/local-auth/start", json={"email": "a@example.org"})
    wrong = f"{(int(outbox.code()) + 1) % 1_000_000:06d}"
    r = await lc.post("/local-auth/verify", json={"email": "a@example.org", "code": wrong})
    assert r.status_code == 400 and r.json()["error"] == "invalid_code"
    assert wrong not in r.text and outbox.code() not in r.text
    # the right code still works afterwards (1 of 5 attempts used)
    r = await lc.post("/local-auth/verify", json={"email": "a@example.org", "code": outbox.code()})
    assert r.status_code == 200


async def test_code_for_another_address_is_refused(lc, outbox):
    await lc.post("/local-auth/start", json={"email": "a@example.org"})
    r = await lc.post("/local-auth/verify", json={"email": "b@example.org", "code": outbox.code()})
    assert r.status_code == 400


async def test_expiry(lc, outbox, clock):
    await lc.post("/local-auth/start", json={"email": "a@example.org"})
    clock.t += 600
    r = await lc.post("/local-auth/verify", json={"email": "a@example.org", "code": outbox.code()})
    assert r.status_code == 400 and r.json()["error"] == "invalid_code"


async def test_five_wrong_attempts_burn_the_code(lc, outbox):
    await lc.post("/local-auth/start", json={"email": "a@example.org"})
    code = outbox.code()
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    for _ in range(5):
        r = await lc.post("/local-auth/verify", json={"email": "a@example.org", "code": wrong})
        assert r.status_code == 400
    r = await lc.post("/local-auth/verify", json={"email": "a@example.org", "code": code})
    assert r.status_code == 400  # the right code no longer works: request a new one


async def test_new_start_replaces_the_previous_code(lc, outbox):
    await lc.post("/local-auth/start", json={"email": "a@example.org"})
    first = outbox.code()
    await lc.post("/local-auth/start", json={"email": "a@example.org"})
    second = outbox.code()
    if first != second:
        r = await lc.post("/local-auth/verify", json={"email": "a@example.org", "code": first})
        assert r.status_code == 400
    r = await lc.post("/local-auth/verify", json={"email": "a@example.org", "code": second})
    assert r.status_code == 200


async def test_rate_limit_per_email_and_per_ip(lc, outbox, clock):
    for _ in range(5):
        assert (
            await lc.post("/local-auth/start", json={"email": "a@example.org"})
        ).status_code == 202
    r = await lc.post("/local-auth/start", json={"email": "a@example.org"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert len(outbox.sent) == 5
    # other addresses from the same client until the per-IP limit (20 per window)
    for i in range(14):
        assert (
            await lc.post("/local-auth/start", json={"email": f"u{i}@example.org"})
        ).status_code == 202
    r = await lc.post("/local-auth/start", json={"email": "fresh@example.org"})
    assert r.status_code == 429
    clock.t += 900  # next window
    assert (await lc.post("/local-auth/start", json={"email": "a@example.org"})).status_code == 202


async def test_verify_is_rate_limited_per_ip(lc, outbox):
    statuses = []
    for i in range(31):
        r = await lc.post(
            "/local-auth/verify", json={"email": f"x{i}@example.org", "code": "000000"}
        )
        statuses.append(r.status_code)
    assert statuses[:30] == [400] * 30 and statuses[30] == 429


async def test_response_does_not_reveal_whether_the_account_exists(lc, outbox):
    await sign_in(lc, outbox, "known@example.org")
    r_known = await lc.post("/local-auth/start", json={"email": "known@example.org"})
    r_new = await lc.post("/local-auth/start", json={"email": "unknown@example.org"})
    assert r_known.status_code == r_new.status_code == 202
    assert r_known.json() == r_new.json()


@pytest.mark.parametrize(
    "path,body",
    [
        ("/local-auth/start", {"email": "not-an-email"}),
        ("/local-auth/start", {"email": "a@example.org", "role": "ADMIN"}),
        ("/local-auth/verify", {"email": "a@example.org", "code": "12345"}),
        ("/local-auth/verify", {"email": "a@example.org", "code": "abcdef"}),
        ("/local-auth/verify", {"email": "a@example.org", "code": "123456", "role": "ADMIN"}),
    ],
)
async def test_validation_422_without_echo(lc, path, body):
    r = await lc.post(path, json=body)
    assert r.status_code == 422
    assert "not-an-email" not in r.text and "abcdef" not in r.text and "ADMIN" not in r.text


async def test_code_never_in_responses_or_logs(lc, lapp, caplog):
    sent: list[str] = []
    caplog.set_level(logging.DEBUG)
    # use the real terminal printer, captured, to prove the ONLY place the code appears
    import io
    from contextlib import redirect_stderr

    err = io.StringIO()
    codes = lapp.state.local_auth
    codes._deliver = lambda e, c: (sent.append(c), print_to_terminal(e, c))
    with redirect_stderr(err):
        r1 = await lc.post("/local-auth/start", json={"email": "logcheck@example.org"})
        r2 = await lc.post("/local-auth/verify", json={"email": "logcheck@example.org",
                                                       "code": sent[-1]})  # fmt: skip
    code = sent[-1]
    line = err.getvalue()
    assert line.count("\n") == 1
    assert line == f"[LOCAL DEV MAIL] sign-in code for logcheck@example.org: {code}\n"
    assert code not in r1.text and code not in r2.text
    token = r2.json()["token"]
    for rec in caplog.records:
        msg = rec.getMessage()
        assert code not in msg and "logcheck@example.org" not in msg and token not in msg


async def test_role_claims_in_tokens_are_ignored(lc, outbox, local_settings):
    now = int(time.time())
    forged = jwt.encode({"sub": "local:abc", "role": "ADMIN", "roles": ["ADMIN"], "iat": now,
                         "exp": now + 300, "email": "x@example.org", "email_verified": True},
                        SECRET, algorithm="HS256")  # fmt: skip
    s = (await lc.get("/auth/session", headers=bearer(forged))).json()
    assert s["user"]["role"] == "CITIZEN"
    h = await sign_in(lc, outbox)
    assert (await lc.get("/auth/session", headers=h)).json()["user"]["role"] == "CITIZEN"


async def test_investigator_entry_allowlisted_vs_not(lc, outbox):
    # the conftest allowlist holds INVESTIGATOR_EMAIL (different casing)
    ok = await sign_in(lc, outbox, INVESTIGATOR_EMAIL.lower())
    r = await lc.post("/auth/entry", json={"entry": "investigator"}, headers=ok)
    assert r.status_code == 200 and r.json()["user"]["role"] == "INVESTIGATOR"
    no = await sign_in(lc, outbox, "someone.else@example.org")
    r = await lc.post("/auth/entry", json={"entry": "investigator", "role": "INVESTIGATOR"},
                      headers=no)  # fmt: skip
    assert r.status_code == 403 and r.json()["error"] == "investigator_not_approved"
    assert (await lc.get("/me", headers=no)).json()["role"] == "CITIZEN"


async def test_admin_only_through_bootstrap(lc, outbox):
    h = await sign_in(lc, outbox, "root@example.org")  # ADMIN_BOOTSTRAP_EMAILS in conftest
    assert (await lc.get("/me", headers=h)).json()["role"] == "ADMIN"


async def test_auth_config_in_local_mode(lc):
    r = await lc.get("/auth/config")
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["clerk"] == {"enabled": False, "publishable_key": None}
    assert cfg["local"]["enabled"] is True and cfg["local"]["dev_only"] is True


async def test_disabled_in_clerk_mode(client, settings):
    for path in ("/local-auth/start", "/local-auth/verify"):
        r = await client.post(path, json={"email": "a@example.org", "code": "123456"})
        assert r.status_code == 404
    cfg = (await client.get("/auth/config")).json()
    assert cfg["local"]["enabled"] is False and cfg["clerk"]["enabled"] is True


async def test_clerk_publishable_key_is_exposed_only_when_publishable(make_app, settings):
    app = make_app(settings=dataclasses.replace(settings, clerk_publishable_key="pk_test_abc123"))
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    assert (await c.get("/auth/config")).json()["clerk"]["publishable_key"] == "pk_test_abc123"
    from complaints.config import ConfigError

    with pytest.raises(ConfigError):
        make_app(settings=dataclasses.replace(settings, clerk_publishable_key="sk_test_secret"))


async def test_demo_mode_also_offers_local_sign_in(settings, analysis):
    s = dataclasses.replace(settings, auth_mode="demo", demo_jwt_secret=SECRET)
    app = create_app(s, analysis=analysis)
    try:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        assert (await c.get("/auth/config")).json()["local"]["enabled"] is True
    finally:
        app.state.store.close()


async def test_test_only_file_outbox_writes_the_code(local_settings, analysis, tmp_path, capsys):
    import json as _json
    import stat

    box = tmp_path / "outbox.jsonl"
    app = create_app(dataclasses.replace(local_settings, local_auth_outbox=box), analysis=analysis)
    try:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        r = await c.post("/local-auth/start", json={"email": "E2E@Example.org"})
        assert r.status_code == 202
        rec = _json.loads(box.read_text().splitlines()[-1])
        assert rec["email"] == "e2e@example.org" and len(rec["code"]) == 6
        assert stat.S_IMODE(box.stat().st_mode) == 0o600
        assert rec["code"] in capsys.readouterr().err  # the terminal line is still printed
        assert rec["code"] not in r.text
        r = await c.post(
            "/local-auth/verify", json={"email": "e2e@example.org", "code": rec["code"]}
        )
        assert r.status_code == 200
    finally:
        app.state.store.close()
