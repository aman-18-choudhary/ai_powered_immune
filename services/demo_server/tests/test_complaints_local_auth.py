"""Local sign-in through the demo server mount (/api/complaints/local-auth/*, dev only)."""

import dataclasses
import io
import re
from contextlib import redirect_stderr

import pytest
from conftest import instant, login, make_settings
from fastapi.testclient import TestClient

from demo_server import complaints_mount
from demo_server.api import create_app

LINE = re.compile(r"^\[LOCAL DEV MAIL\] sign-in code for (\S+): (\d{6})$")


def _settings(tmp_path, **over):
    base = complaints_mount.complaints_settings(make_settings(tmp_path))
    return dataclasses.replace(base, **over)


@pytest.fixture
def client(tmp_path):
    cs = _settings(tmp_path, auth_mode="local",
                   investigator_allowlist=frozenset({"owner@example.org"}),
                   admin_bootstrap_emails=frozenset())  # fmt: skip
    app = create_app(make_settings(tmp_path), sleep=instant, complaints_settings=cs)
    with TestClient(app) as c:
        yield c


def sign_in(c: TestClient, email: str) -> dict[str, str]:
    err = io.StringIO()
    with redirect_stderr(err):
        r = c.post("/api/complaints/local-auth/start", json={"email": email})
    assert r.status_code == 202, r.text
    lines = err.getvalue().splitlines()
    assert len(lines) == 1
    m = LINE.fullmatch(lines[0])
    assert m and m.group(1) == email
    code = m.group(2)
    assert code not in r.text
    r = c.post("/api/complaints/local-auth/verify", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    assert code not in r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_auth_config_reports_local_sign_in(client):
    cfg = client.get("/api/complaints/auth/config").json()
    assert cfg["local"]["enabled"] is True and cfg["clerk"]["enabled"] is False


def test_local_sign_in_and_investigator_entry(client):
    h = sign_in(client, "owner@example.org")
    s = client.get("/api/complaints/auth/session", headers=h).json()
    assert s["user"]["role"] == "CITIZEN" and s["user"]["email_verified"] is True
    r = client.post("/api/complaints/auth/entry", json={"entry": "investigator"}, headers=h)
    assert r.status_code == 200 and r.json()["user"]["role"] == "INVESTIGATOR"
    other = sign_in(client, "stranger@example.org")
    r = client.post("/api/complaints/auth/entry", json={"entry": "investigator"}, headers=other)
    assert r.status_code == 403


def test_local_tokens_do_not_open_simulator_routes(client):
    h = sign_in(client, "someone@example.org")
    assert client.get("/api/demo/state", headers=h).status_code == 401


def test_demo_login_still_citizen_in_local_mode(client):
    h = login(client, "admin")
    assert client.get("/api/complaints/me", headers=h).json()["role"] == "CITIZEN"


def test_local_sign_in_absent_in_clerk_mode(tmp_path):
    cs = _settings(tmp_path, auth_mode="clerk", clerk_issuer="https://clerk.example.test",
                   clerk_jwks_url="https://clerk.example.test/.well-known/jwks.json",
                   clerk_publishable_key="pk_test_Y2xlcmsuZXhhbXBsZQ")  # fmt: skip
    app = create_app(make_settings(tmp_path), sleep=instant, complaints_settings=cs)
    with TestClient(app) as c:
        r = c.post("/api/complaints/local-auth/start", json={"email": "a@example.org"})
        assert r.status_code == 404
        cfg = c.get("/api/complaints/auth/config").json()
        assert cfg["local"]["enabled"] is False
        assert cfg["clerk"] == {"enabled": True, "publishable_key": "pk_test_Y2xlcmsuZXhhbXBsZQ"}
