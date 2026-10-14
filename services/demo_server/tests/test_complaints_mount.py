"""The complaints service mounted at /api/complaints: reachability, demo tokens map to CITIZEN,
body caps (JSON 64 KiB, evidence uploads up to the service's own per-type caps), CORS methods,
lifespan, defaults."""

import dataclasses
from pathlib import Path

import pytest
from complaints.config import Settings as ComplaintsSettings
from conftest import instant, login, make_settings
from fastapi.testclient import TestClient

from demo_server import complaints_mount
from demo_server.api import create_app
from demo_server.config import ConfigError, Settings

MIN = {
    "category": "PHISHING",
    "title": "Fake bank SMS",
    "description": "I received an SMS asking me to click a link and update KYC.",
}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 32


@pytest.fixture
def client(tmp_path):
    app = create_app(make_settings(tmp_path), sleep=instant)
    with TestClient(app) as c:
        yield c


def test_health_and_unknown_paths(client):
    assert client.get("/api/complaints/healthz").status_code == 200
    r = client.get("/api/complaints/no-such-route")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    assert client.get("/api/health").json()["status"] == "ok"  # demo routes unaffected


def test_auth_required(client):
    r = client.get("/api/complaints/auth/session")
    assert r.status_code == 401 and r.json()["error"] == "authentication_required"


@pytest.mark.parametrize("role", ["citizen", "officer", "analyst", "admin"])
def test_demo_login_tokens_are_citizens(client, role):
    h = login(client, role)
    r = client.get("/api/complaints/auth/session", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"] == "CITIZEN"
    assert "admin:users" not in r.json()["capabilities"]
    r = client.post("/api/complaints/auth/entry", json={"entry": "investigator"}, headers=h)
    assert r.status_code == 403 and r.json()["error"] == "investigator_not_approved"


def test_complaint_flow_through_the_mount(client, tmp_path):
    h = login(client, "citizen")
    r = client.post("/api/complaints/complaints", json={**MIN, "ifsc": "SBIN0001234"}, headers=h)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["ifsc"] == "SBIN0001234"
    lst = client.get("/api/complaints/complaints", headers=h).json()
    assert [i["id"] for i in lst["items"]] == [cid]
    me = client.get("/api/complaints/me", headers=h).json()
    assert me["role"] == "CITIZEN"
    r = client.patch("/api/complaints/me", json={"privacy": {"allow_case_matching": False}},
                     headers=h)  # fmt: skip
    assert r.status_code == 200
    # persisted in the SQLite file under the work dir
    assert (tmp_path / "complaints" / "complaints.db").is_file()
    # a different demo citizen cannot see it (404, not 403)
    other = login(client, "citizen")
    assert client.get(f"/api/complaints/complaints/{cid}", headers=other).status_code == 404


def test_evidence_upload_above_the_demo_json_cap(client, tmp_path):
    h = login(client, "citizen")
    cid = client.post("/api/complaints/complaints", json=MIN, headers=h).json()["id"]
    big = PNG + b"\x00" * (200 * 1024)  # > the demo server's 64 KiB default body cap
    r = client.post(f"/api/complaints/complaints/{cid}/evidence",
                    files={"file": ("shot.png", big, "image/png")}, headers=h)  # fmt: skip
    assert r.status_code == 201, r.text
    assert r.json()["size_bytes"] == len(big)
    ev = r.json()["id"]
    dl = client.get(f"/api/complaints/complaints/{cid}/evidence/{ev}/download", headers=h)
    assert dl.status_code == 200 and dl.content == big
    r = client.delete(f"/api/complaints/complaints/{cid}/evidence/{ev}", headers=h)
    assert r.status_code == 200 and r.json()["file_removed"] is True


def test_service_caps_still_apply(tmp_path):
    cs = dataclasses.replace(
        complaints_mount.complaints_settings(make_settings(tmp_path)), max_image_bytes=100_000
    )
    app = create_app(make_settings(tmp_path), sleep=instant, complaints_settings=cs)
    with TestClient(app) as c:
        h = login(c, "citizen")
        cid = c.post("/api/complaints/complaints", json=MIN, headers=h).json()["id"]
        r = c.post(f"/api/complaints/complaints/{cid}/evidence",
                   files={"file": ("shot.png", PNG + b"\x00" * 150_000, "image/png")},
                   headers=h)  # fmt: skip
        assert r.status_code == 413
        # beyond the edge cap (largest per-file cap + multipart overhead): refused up front
        edge = complaints_mount.upload_cap(cs)
        r = c.post(f"/api/complaints/complaints/{cid}/evidence", content=b"x" * (edge + 1),
                   headers={**h, "Content-Type": "multipart/form-data; boundary=x"})  # fmt: skip
        assert r.status_code == 413


def test_json_body_cap_on_complaints_routes(client):
    h = login(client, "citizen")
    body = {**MIN, "description": "d" * (70 * 1024)}
    r = client.post("/api/complaints/complaints", json=body, headers=h)
    assert r.status_code == 413
    # the evidence cap does not widen other complaints paths
    r = client.post("/api/complaints/analyze", json={"text": "x" * (70 * 1024)}, headers=h)
    assert r.status_code == 413


def test_cors_preflight_allows_patch_and_delete(client):
    for method in ("PATCH", "DELETE", "PUT"):
        r = client.options(
            "/api/complaints/me",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": method,
                     "Access-Control-Request-Headers": "authorization,content-type"},
        )  # fmt: skip
        assert r.status_code == 200, (method, r.text)
        assert method in r.headers["access-control-allow-methods"]


def test_complaints_db_is_closed_on_shutdown(tmp_path):
    app = create_app(make_settings(tmp_path), sleep=instant)
    closed = []
    store = app.state.complaints.state.store
    orig = store.close
    store.close = lambda: (closed.append(1), orig())[1]
    with TestClient(app):
        pass
    assert closed == [1]


def test_from_env_defaults_to_a_local_data_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Settings.from_env({})
    data = (tmp_path / ".data" / "complaints").resolve()
    assert s.complaints_database_url == f"sqlite:///{data / 'complaints.db'}"
    assert s.complaints_evidence_dir == data / "evidence"
    assert s.complaints_auth_mode is None
    assert not (tmp_path / ".data").exists()  # nothing is created until the app is built
    s2 = Settings.from_env({"COMPLAINTS_DATABASE_URL": "sqlite:///x.db",
                            "COMPLAINTS_EVIDENCE_DIR": "/tmp/ev", "COMPLAINTS_AUTH_MODE": "Demo"})  # fmt: skip
    assert s2.complaints_database_url == "sqlite:///x.db"
    assert s2.complaints_evidence_dir == Path("/tmp/ev") and s2.complaints_auth_mode == "demo"


def test_auth_mode_selection(tmp_path, monkeypatch):
    for k in ("CLERK_ISSUER", "CLERK_JWKS_URL", "COMPLAINTS_AUTH_MODE"):
        monkeypatch.delenv(k, raising=False)
    s = complaints_mount.complaints_settings(make_settings(tmp_path))
    assert s.auth_mode == "demo" and s.demo_jwt_secret == make_settings(tmp_path).jwt_secret
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.example.test")
    monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.example.test/.well-known/jwks.json")
    s = complaints_mount.complaints_settings(make_settings(tmp_path))
    assert s.auth_mode == "clerk"
    with pytest.raises(ConfigError):
        complaints_mount.complaints_settings(make_settings(tmp_path, complaints_auth_mode="none"))


def test_invalid_complaints_config_refuses_to_start(tmp_path):
    bad = ComplaintsSettings(database_url="sqlite://", auth_mode="clerk",
                             evidence_dir=tmp_path / "ev")  # no issuer / JWKS  # fmt: skip
    with pytest.raises(ConfigError):
        create_app(make_settings(tmp_path), sleep=instant, complaints_settings=bad)
