import logging

import pytest
from conftest import instant, login, make_settings
from fastapi.testclient import TestClient

from demo_server import security
from demo_server.api import create_app


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    s = make_settings(
        tmp_path_factory.mktemp("sec"),
        rate_limits={"citizen": 4, "analyst": 100, "officer": 100, "admin": 100, "anon": 100},
    )
    with TestClient(create_app(s, sleep=instant)) as c:
        yield c


def test_security_headers_everywhere(client):
    for path in ("/api/health", "/", "/api/nope"):
        r = client.get(path)
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY"
    assert client.get("/api/health").headers["cache-control"] == "no-store"


def test_request_id_sanitised(client):
    r = client.get("/api/health", headers={"X-Request-Id": "abc-123_x.y"})
    assert r.headers["x-request-id"] == "abc-123_x.y"
    for bad in ("has space", "x" * 65, "semi;colon", "<script>"):
        r = client.get("/api/health", headers={"X-Request-Id": bad})
        rid = r.headers["x-request-id"]
        assert rid != bad and security.REQUEST_ID_RE.fullmatch(rid) is None or len(rid) == 36
    assert len(client.get("/api/health").headers["x-request-id"]) == 36  # generated uuid4


def test_body_cap_413_declared_and_streamed(client):
    h = login(client, "officer")
    big = {"message": "a" * 70_000}
    r = client.post("/api/citizen/assess", json=big, headers=h)
    assert r.status_code == 413 and r.json() == {"error": "payload_too_large"}

    def gen():  # chunked: no Content-Length is declared
        for _ in range(80):
            yield b'{"message": "' if _ == 0 else b"a" * 1024
        yield b'"}'

    r = client.post(
        "/api/citizen/assess", content=gen(), headers={**h, "Content-Type": "application/json"}
    )
    assert r.status_code == 413
    ok = client.post("/api/citizen/assess", json={"message": "a" * 4000}, headers=h)
    assert ok.status_code == 200  # a 4 KB body is well under the 64 KiB cap


def test_validation_errors_never_echo_input(client):
    h = login(client, "officer")
    secret = "SUPER-SECRET-VALUE-123"
    for path, body in (
        ("/api/demo/start", {"seed": secret}),
        ("/api/demo/start", {"speed": 999, secret: 1}),  # unknown KEY text is not echoed either
        ("/api/demo/control", {"action": secret}),
        ("/api/cases", {"case_id": secret + " bad id"}),
        ("/api/verify", {"case_id": secret * 10}),
        ("/api/citizen/assess", {"message": 5, "lang": secret}),
    ):
        r = client.post(path, json=body, headers=h)
        assert r.status_code == 422, (path, r.text)
        assert secret not in r.text and '"input"' not in r.text and '"ctx"' not in r.text
        assert r.json()["error"] == "invalid_request"
    r = client.post("/api/demo/start", content=b"{not json " + secret.encode(), headers=h)
    assert r.status_code == 422 and secret not in r.text


def test_unknown_route_and_method_have_error_shape(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json() == {"error": "not_found"}
    r = client.delete("/api/health")
    assert r.status_code == 405 and r.json()["error"] == "method_not_allowed"


def test_rate_limit_per_role_with_retry_after_and_fail_open(client):
    h = login(client, "citizen")
    codes = [client.get("/api/benchmark/summary", headers=h).status_code for _ in range(6)]
    assert codes[:4] == [200] * 4 and 429 in codes[4:]
    r = client.get("/api/benchmark/summary", headers=h)
    assert r.status_code == 429 and r.json() == {"error": "rate_limited"}
    assert 1 <= int(r.headers["retry-after"]) <= 60
    # another role has its own budget
    assert client.get("/api/benchmark/summary", headers=login(client, "officer")).status_code == 200


def test_rate_limiter_fails_open():
    class Boom(dict):
        def get(self, *a):
            raise RuntimeError("store down")

    rl = security.RateLimiter({"officer": 1})
    rl._counts = Boom()  # type: ignore[assignment]
    assert rl.check("officer", "x") is None and rl.check("officer", "x") is None


def test_rate_limiter_window_rolls():
    t = [1000.0]
    rl = security.RateLimiter({"officer": 2}, window_s=60, clock=lambda: t[0])
    assert rl.check("officer", "k") is None and rl.check("officer", "k") is None
    wait = rl.check("officer", "k")
    assert wait is not None and 1 <= wait <= 60
    t[0] += 61
    assert rl.check("officer", "k") is None


def test_cors_only_for_explicit_origins(client):
    ok = client.options(
        "/api/demo/state",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET",
                 "Access-Control-Request-Headers": "authorization"},
    )  # fmt: skip
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-credentials" not in ok.headers
    bad = client.options(
        "/api/demo/state",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in bad.headers
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_access_log_has_no_query_body_or_token(client, caplog):
    h = login(client, "officer")
    with caplog.at_level(logging.DEBUG):
        client.post(
            "/api/citizen/assess?token=QUERYSECRET&x=1",
            json={"message": "BODYSECRET-DO-NOT-LOG"},
            headers=h,
        )
    mine = [r for r in caplog.records if r.name.startswith("demo_server")]
    text = "\n".join(r.getMessage() for r in mine)
    for secret in ("QUERYSECRET", "BODYSECRET", h["Authorization"].split()[1]):
        assert secret not in text
    line = next(r.getMessage() for r in mine if r.name == "demo_server.access")
    assert "route=/api/citizen/assess" in line and "status=200" in line and "rid=" in line


def test_static_console_and_spa_fallback(tmp_path):
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>console</html>")
    (root / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside the root")
    s = make_settings(tmp_path / "w", static_dir=root)
    with TestClient(create_app(s, sleep=instant)) as c:
        assert "console" in c.get("/").text
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert "console" in c.get("/command").text  # SPA route falls back to index.html
        assert c.get("/missing.js").status_code == 404
        assert c.get("/%2e%2e/secret.txt").status_code in (404, 200)
        assert "outside the root" not in c.get("/%2e%2e/secret.txt").text
        assert "outside the root" not in c.get("/..%2fsecret.txt").text
        assert c.get("/api/nope").json() == {"error": "not_found"}


def test_placeholder_page_without_console(client):
    r = client.get("/")
    assert r.status_code == 200 and "SIMULATION" in r.text
