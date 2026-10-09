import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from demo_server.api import create_app
from demo_server.config import Settings

SECRET = "unit-test-secret-" + "x" * 32


async def instant(_: float) -> None:
    """Injected pacing sleep: runs finish immediately."""
    return None


def make_settings(tmp: Path, **kw) -> Settings:
    base = dict(jwt_secret=SECRET, work_dir=tmp)
    base.update(kw)
    return Settings(**base)


def login(c: TestClient, role: str) -> dict[str, str]:
    r = c.post("/api/auth/demo-login", json={"role": role})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def wait_status(c: TestClient, h: dict[str, str], want: tuple[str, ...], timeout: float = 60.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = c.get("/api/demo/state", headers=h).json()
        if s["status"] in want:
            return s
        time.sleep(0.02)
    raise AssertionError(f"timeout waiting for {want}: {s}")


@pytest.fixture
def app_factory(tmp_path_factory):
    def make(**kw):
        return create_app(make_settings(tmp_path_factory.mktemp("demo"), **kw), sleep=instant)

    return make


@pytest.fixture(scope="module")
def hero_client(tmp_path_factory) -> Iterator[TestClient]:
    """One app with a finished seed-1 hero run, shared read-only by many tests."""
    app = create_app(make_settings(tmp_path_factory.mktemp("hero")), sleep=instant)
    with TestClient(app) as c:
        off = login(c, "officer")
        r = c.post("/api/demo/start", json={"seed": 1, "speed": 20, "mode": "hero"}, headers=off)
        assert r.status_code == 200, r.text
        wait_status(c, off, ("finished", "error"))
        yield c


@pytest.fixture
def fresh_client(app_factory) -> Iterator[TestClient]:
    with TestClient(app_factory()) as c:
        yield c
