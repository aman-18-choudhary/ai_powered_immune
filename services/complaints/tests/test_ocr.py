"""Screenshot text (OCR, Phase 6). The engine is a fake unless COMPLAINTS_TEST_REAL_OCR=1, which
reads tests/fixtures/synthetic_kyc_sms.png (a SYNTHETIC message rendered for this test) with the
local tesseract."""

import os
import shutil
import socket
from pathlib import Path

import httpx
import pytest

from complaints.analysis import ComplaintAnalysisPipeline
from complaints.ocr import NoOcr, TesseractEngine, normalise_lines

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_kyc_sms.png"
SCAM_TEXT = ("Dear customer, your SBI account will be blocked today. Update your KYC immediately "
             "at http://sbi-kyc-update.example/login and share the OTP to keep it active.")  # fmt: skip
DESC = "I received a message about my bank account and attached a screenshot of it."


class FakeOcr:
    def __init__(self, text=SCAM_TEXT, conf=91.5, fail=False):
        self.text, self.conf, self.fail, self.calls = text, conf, fail, 0

    def status(self):
        return {"status": "ready", "reason": None, "engine": "tesseract", "version": "fake 5",
                "languages": ["eng", "hin"]}  # fmt: skip

    def read(self, path):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return self.text, self.conf


def test_wrapped_lines_join_and_hyphenated_links_rejoin():
    assert normalise_lines(["at http://sbi-kyc-", "update.example/login and", "", " share"]) == (
        "at http://sbi-kyc-update.example/login and share")  # fmt: skip


def owner(tok):
    return tok("a")


async def _setup(make_app, tok, engine):
    app = make_app(analysis=ComplaintAnalysisPipeline(category_loader=lambda: None),
                   ocr_engine=engine)  # fmt: skip
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    r = await c.post("/complaints", headers=owner(tok), json={
        "category": "PHISHING", "title": "Bank message", "description": DESC})  # fmt: skip
    cid = r.json()["id"]
    up = await c.post(f"/complaints/{cid}/evidence", headers=owner(tok),
                      files={"file": ("shot.png", FIXTURE.read_bytes(), "image/png")})  # fmt: skip
    assert up.status_code == 201, up.text
    return app, c, cid, up.json()["id"]


async def test_screenshot_text_is_scored_and_points_to_the_image(make_app, tok, inv):
    pytest.importorskip("call_guard")
    eng = FakeOcr()
    app, c, cid, eid = await _setup(make_app, tok, eng)
    a = (await c.post(f"/complaints/{cid}/analysis", headers=owner(tok))).json()
    shots = [i for i in a["indicators"] if i["stage"] == "screenshot_text"]
    assert shots and all(i["evidence"]["evidence_id"] == eid for i in shots)
    assert any(i["source"] == "rule" for i in shots)
    assert "machine-read" in shots[0]["evidence"]["note"]
    assert a["capabilities"]["screenshot_ocr"] == "available"
    assert a["risk_level"] in ("MEDIUM", "HIGH", "CRITICAL")  # the description alone is not
    # the evidence view says the text was read, and the text endpoint returns it
    d = (await c.get(f"/complaints/{cid}", headers=owner(tok))).json()
    ocr = d["evidence"][0]["ocr"]
    assert ocr["status"] == "extracted" and ocr["text_available"] and ocr["mean_confidence"] == 91.5
    t = (await c.get(f"/complaints/{cid}/evidence/{eid}/ocr", headers=inv)).json()
    assert t["text"] == SCAM_TEXT and "may contain recognition errors" in t["note"]
    assert (await c.get(f"/complaints/{cid}/evidence/{eid}/ocr", headers=tok("b"))).status_code == 404
    # read once: a second analysis reuses the stored text
    await c.post(f"/complaints/{cid}/analysis", headers=owner(tok))
    assert eng.calls == 1


async def test_no_engine_is_reported_and_never_gives_a_low_result(make_app, tok):
    pytest.importorskip("call_guard")
    _, c, cid, eid = await _setup(make_app, tok, NoOcr("engine_not_installed"))
    a = (await c.post(f"/complaints/{cid}/analysis", headers=owner(tok))).json()
    assert a["capabilities"]["screenshot_ocr"] == "unavailable"
    assert a["capability_reasons"]["screenshot_ocr"] == "engine_unavailable"
    assert a["risk_level"] != "LOW"
    d = (await c.get(f"/complaints/{cid}", headers=owner(tok))).json()
    assert d["evidence"][0]["ocr"]["status"] == "engine_unavailable"
    r = await c.get(f"/complaints/{cid}/evidence/{eid}/ocr", headers=owner(tok))
    assert r.status_code == 404 and r.json()["error"] == "ocr_text_not_available"


async def test_failure_and_no_text(make_app, tok):
    pytest.importorskip("call_guard")
    _, c, cid, _ = await _setup(make_app, tok, FakeOcr(fail=True))
    a = (await c.post(f"/complaints/{cid}/analysis", headers=owner(tok))).json()
    assert a["capability_reasons"]["screenshot_ocr"] == "failed"
    _, c2, cid2, _ = await _setup(make_app, tok, FakeOcr(text=""))
    a2 = (await c2.post(f"/complaints/{cid2}/analysis", headers=owner(tok))).json()
    assert a2["capabilities"]["screenshot_ocr"] == "available"
    assert not [i for i in a2["indicators"] if i["stage"] == "screenshot_text"]


async def test_deleting_the_evidence_deletes_its_text(app, make_app, tok):
    pytest.importorskip("call_guard")
    app2, c, cid, eid = await _setup(make_app, tok, FakeOcr())
    await c.post(f"/complaints/{cid}/analysis", headers=owner(tok))
    assert eid in app2.state.store.list_ocr(cid)
    r = await c.delete(f"/complaints/{cid}/evidence/{eid}", headers=owner(tok))
    assert r.status_code == 200
    assert app2.state.store.list_ocr(cid) == {}


async def test_ocr_text_is_never_logged(make_app, tok, caplog):
    pytest.importorskip("call_guard")
    caplog.set_level("DEBUG")
    _, c, cid, _ = await _setup(make_app, tok, FakeOcr())
    await c.post(f"/complaints/{cid}/analysis", headers=owner(tok))
    assert "sbi-kyc-update" not in caplog.text and "Dear customer" not in caplog.text


@pytest.mark.skipif(os.getenv("COMPLAINTS_TEST_REAL_OCR") != "1" or not shutil.which("tesseract"),
                    reason="set COMPLAINTS_TEST_REAL_OCR=1 with tesseract installed")  # fmt: skip
def test_real_tesseract_reads_the_synthetic_screenshot_offline(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    eng = TesseractEngine()
    st = eng.status()
    assert st["status"] == "ready" and "eng" in st["languages"]
    text, conf = eng.read(FIXTURE)
    for word in ("blocked", "KYC", "OTP", "http://sbi-kyc-update.example/login"):
        assert word in text
    assert conf is not None and conf > 60
