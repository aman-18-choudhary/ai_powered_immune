import pytest
from conftest import make_settings
from fastapi.testclient import TestClient

from demo_server.api import create_app


@pytest.fixture
def static_client(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>spa</html>")
    (root / "assets" / "app-abc123.js").write_text("x=1")
    app = create_app(make_settings(tmp_path / "w", static_dir=root))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.parametrize(
    "path",
    [
        "/%00", "/a%00b", "/" + "a" * 300, "/" + "a" * 5000, "/%ff%fe", "/%c0%af",
        "/..%2f..%2fetc/passwd", "/%2e%2e/%2e%2e/etc/passwd", "/assets/%00.js",
        "/assets/../../etc/passwd", "/%2e%2e%5c%2e%2e%5cetc", "/" + "/".join(["a"] * 400),
        "/\\x00", "/assets/" + "b" * 400 + ".js",
    ],
    ids=lambda p: p[:24],
)  # fmt: skip
def test_static_route_never_500s(static_client, path):
    r = static_client.get(path)
    assert r.status_code in (200, 404), (path, r.status_code)
    if r.status_code == 404:
        assert r.json() == {"error": "not_found"}


def test_static_still_serves_assets_and_spa(static_client):
    assert static_client.get("/assets/app-abc123.js").text == "x=1"
    assert "spa" in static_client.get("/some/route").text


def test_unhandled_error_is_generic_500_with_internal_error_body(tmp_path, monkeypatch):
    from conftest import login

    from demo_server import benchmark

    def boom():
        raise RuntimeError("secret detail /etc/passwd")

    monkeypatch.setattr(benchmark, "encoded_summary", boom)
    app = create_app(make_settings(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/benchmark/summary", headers=login(c, "citizen"))
        assert r.status_code == 500
        assert r.json() == {"error": "internal_error"}
        assert "secret" not in r.text
        assert r.headers["x-content-type-options"] == "nosniff"
        big = c.post("/api/auth/demo-login", content=b"x" * 70_000)
        assert big.status_code == 413 and big.json() == {"error": "payload_too_large"}
