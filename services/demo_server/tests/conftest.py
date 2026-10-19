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


# ---- the engine's documented determinism contract (demo_server/engine.py docstring)
# Everything except ledger.entry events is identical for a given seed and mode. ledger.entry
# events wait at most LEDGER_WAIT_S of wall time for the ledger consumer; when it lags (seen
# intermittently on GitHub's Linux runners with any dependency versions, 2026-10-19) they appear
# at a later sync point, which shifts their position and the ids and ledger counters after them.
# These helpers take events already normalised by the caller.
def split_ledger(events):
    """(non-ledger events without ids or ledger counters, ledger entries as (seq, type))."""
    other, ledger = [], []
    for e in events:
        if e["kind"] == "ledger.entry":
            ledger.append((e["seq"], e["event_type"]))
            continue
        e = {k: v for k, v in e.items() if k != "id"}
        if "counters" in e:
            e["counters"] = {k: v for k, v in e["counters"].items() if k != "ledger_entries"}
        other.append(e)
    return other, ledger


def _ledger_prefix(a, b):
    shorter, longer = sorted((a, b), key=len)
    assert longer[: len(shorter)] == shorter


def assert_same_run(a_events, b_events):
    """Two runs of one seed and mode: same events; same ledger sequence (one may lag at the end)."""
    a, b = split_ledger(a_events), split_ledger(b_events)
    assert a[0] == b[0]
    _ledger_prefix(a[1], b[1])


def assert_run_prefix(prefix_events, full_events):
    """A replay up to some t is the start of the full run, by the same contract."""
    p, f = split_ledger(prefix_events), split_ledger(full_events)
    assert p[0] == f[0][: len(p[0])]
    _ledger_prefix(p[1], f[1])
