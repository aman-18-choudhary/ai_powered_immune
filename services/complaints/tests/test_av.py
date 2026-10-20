"""Malware scanning of uploads (Phase 14). A fake clamd speaks the real INSTREAM protocol on a
local socket; COMPLAINTS_TEST_CLAMD=<address> also runs the opt-in test against a real clamd
with the standard (harmless) EICAR test string."""

import dataclasses
import os
import socket
import struct
import threading
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa

from complaints.av import ClamdScanner, ScanUnavailable
from complaints.config import ConfigError, Settings
from complaints.schema import complaint_evidence

EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
PNG = (Path(__file__).parent / "fixtures" / "synthetic_kyc_sms.png").read_bytes()


class FakeClamd:
    """Minimal clamd: zPING -> PONG, zVERSION, zINSTREAM (FOUND when EICAR is in the stream)."""

    def __init__(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.address = f"127.0.0.1:{self.srv.getsockname()[1]}"
        self.scanned = 0
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            with conn:
                cmd = b""
                while not cmd.endswith(b"\x00"):
                    cmd += conn.recv(1)
                if cmd == b"zPING\x00":
                    conn.sendall(b"PONG\x00")
                elif cmd == b"zVERSION\x00":
                    conn.sendall(b"ClamAV 1.5.4/fake\x00")
                elif cmd == b"zINSTREAM\x00":
                    data = b""
                    while True:
                        n = struct.unpack(">I", self._exact(conn, 4))[0]
                        if n == 0:
                            break
                        data += self._exact(conn, n)
                    self.scanned += 1
                    conn.sendall(b"stream: Eicar-Test-Signature FOUND\x00" if EICAR in data
                                 else b"stream: OK\x00")  # fmt: skip

    @staticmethod
    def _exact(conn, n):
        buf = b""
        while len(buf) < n:
            buf += conn.recv(n - len(buf))
        return buf

    def close(self):
        self.srv.close()


@pytest.fixture
def clamd():
    c = FakeClamd()
    yield c
    c.close()


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def new_complaint(c, tok):
    r = await c.post("/complaints", headers=tok("a"), json={
        "category": "PHISHING", "title": "Bank message",
        "description": "I received a message about my bank account; screenshot attached."})  # fmt: skip
    return r.json()["id"]


async def upload(c, tok, cid, data):
    return await c.post(f"/complaints/{cid}/evidence", headers=tok("a"),
                        files={"file": ("shot.png", data, "image/png")})  # fmt: skip


def stored_files(settings):
    return [p for p in settings.evidence_dir.rglob("*") if p.is_file() and ".tmp" not in p.parts]


async def test_clean_files_are_stored_and_marked_clean(make_app, settings, tok, clamd):
    app = make_app(settings=dataclasses.replace(settings, clamd_address=clamd.address))
    c = client_for(app)
    cid = await new_complaint(c, tok)
    r = await upload(c, tok, cid, PNG)
    assert r.status_code == 201 and r.json()["scan"] == "clean" and clamd.scanned == 1


async def test_infected_files_are_refused_and_nothing_is_kept(make_app, settings, tok, clamd):
    app = make_app(settings=dataclasses.replace(settings, clamd_address=clamd.address))
    c = client_for(app)
    cid = await new_complaint(c, tok)
    r = await upload(c, tok, cid, PNG + EICAR)  # a PNG carrying the EICAR test string
    assert r.status_code == 422 and r.json()["error"] == "evidence_rejected_malware"
    assert stored_files(settings) == []
    assert not list((settings.evidence_dir / ".tmp").glob("*"))
    with app.state.store.engine.connect() as conn:
        assert conn.execute(sa.select(complaint_evidence)).all() == []


async def test_scanner_down_optional_vs_required(make_app, settings, tok):
    down = "127.0.0.1:1"  # nothing listens there
    app = make_app(settings=dataclasses.replace(settings, clamd_address=down))
    c = client_for(app)
    cid = await new_complaint(c, tok)
    r = await upload(c, tok, cid, PNG)
    assert r.status_code == 201 and r.json()["scan"] == "not_scanned"
    strict = make_app(settings=dataclasses.replace(settings, clamd_address=down, av_required=True))
    c2 = client_for(strict)
    cid2 = await new_complaint(c2, tok)
    r = await upload(c2, tok, cid2, PNG + b"\x01")
    assert r.status_code == 503 and r.json()["error"] == "evidence_scan_unavailable"


async def test_without_a_scanner_nothing_changes(make_app, tok):
    c = client_for(make_app())
    cid = await new_complaint(c, tok)
    r = await upload(c, tok, cid, PNG)
    assert r.status_code == 201 and r.json()["scan"] == "not_configured"


async def test_health_reports_the_scanner(make_app, settings, tok, admin, clamd):
    app = make_app(settings=dataclasses.replace(settings, clamd_address=clamd.address))
    h = (await client_for(app).get("/admin/health", headers=admin)).json()
    assert h["malware_scanner"]["status"] == "ready" and "ClamAV" in h["malware_scanner"]["version"]


def test_required_needs_an_address(tmp_path):
    s = Settings(database_url="sqlite://", evidence_dir=tmp_path, auth_mode="clerk",
                 clerk_issuer="https://c.example", clerk_jwks_url="https://c.example/j",
                 av_required=True)  # fmt: skip
    with pytest.raises(ConfigError, match="COMPLAINTS_CLAMD"):
        s.validate()


def test_unreachable_scanner_raises(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"x")
    with pytest.raises(ScanUnavailable):
        ClamdScanner("127.0.0.1:1", timeout_s=1).scan_file(p)


@pytest.mark.skipif(not os.getenv("COMPLAINTS_TEST_CLAMD"),
                    reason="set COMPLAINTS_TEST_CLAMD to a running clamd (unix:/path or host:port)")  # fmt: skip
def test_real_clamd_detects_eicar_and_passes_a_clean_image(tmp_path):
    scanner = ClamdScanner(os.environ["COMPLAINTS_TEST_CLAMD"])
    assert scanner.status()["status"] == "ready"
    bad = tmp_path / "eicar.txt"
    bad.write_bytes(EICAR)
    verdict, sig = scanner.scan_file(bad)
    assert verdict == "infected" and "Eicar" in (sig or "")
    good = tmp_path / "shot.png"
    good.write_bytes(PNG)
    assert scanner.scan_file(good) == ("clean", None)
