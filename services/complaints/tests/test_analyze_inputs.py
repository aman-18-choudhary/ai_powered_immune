"""POST /analyze structured inputs: text + optional url / upi_id / phone / email / amount /
payment_method (the older suspicious_url / suspect_phone / suspect_email names still work),
validation without echo, per-input capabilities, same pipeline and provenance, nothing stored."""

import httpx
import pytest
from sqlalchemy import text as sql_text
from test_pipeline_v2 import PHISH, StubCategory, StubScorer, pipe


@pytest.fixture
def app_cl(make_app):
    app = make_app(analysis=pipe(StubScorer(0.6), StubCategory("PHISHING", 0.6)))
    return app, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _rows(app) -> int:
    with app.state.store.engine.connect() as c:
        return sum(
            c.execute(sql_text(f"select count(*) from {t}")).scalar_one()
            for t in ("complaints", "analyses", "alerts", "complaint_evidence")
        )


async def test_short_names_accepted_and_reported_per_input(app_cl, tok):
    app, cl = app_cl
    await cl.get("/auth/session", headers=tok("a"))
    before = _rows(app)
    body = {
        "text": PHISH, "url": "http://sbi-kyc.example.top/login", "upi_id": "refund@ybl",
        "phone": "+91 70429 18356", "email": "help@bank-help.top", "amount": "4999.50",
        "payment_method": "UPI",
    }  # fmt: skip
    r = await cl.post("/analyze", json=body, headers=tok("a"))
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["persisted"] is False and _rows(app) == before
    ic = a["input_capabilities"]
    assert ic["text"]["status"] == "analysed"
    assert ic["url"]["status"] == "analysed" and ic["url"]["method"] == "url_heuristics"
    assert ic["upi_id"]["status"] == "format_checked"
    assert ic["phone"]["status"] == "format_checked" and ic["email"]["status"] == "format_checked"
    assert ic["amount"]["status"] == "context_only"
    assert ic["payment_method"]["status"] == "context_only"
    # audio is analysed only as complaint evidence (no claim that no engine exists)
    assert ic["audio"]["status"] == "not_accepted_here"
    assert "attached to a complaint" in ic["audio"]["reason"]
    assert "no speech-to-text engine" not in ic["audio"]["reason"]
    assert ic["screenshot"]["status"] == "unavailable"
    assert "no OCR engine is installed" in ic["screenshot"]["reason"]
    url_ind = [i for i in a["indicators"] if i["stage"] == "url_heuristics"]
    assert url_ind and url_ind[0]["evidence"]["field"] == "url"
    fields = {c["evidence"]["field"] for c in a["entity_checks"]["checks"]}
    assert fields == {"phone", "upi_id", "email"}
    assert a["provenance"]["ran"] and a["status"] in ("COMPLETED", "PARTIAL")


async def test_not_provided_inputs(app_cl, tok):
    _, cl = app_cl
    a = (await cl.post("/analyze", json={"text": PHISH}, headers=tok("a"))).json()
    ic = a["input_capabilities"]
    for k in ("url", "upi_id", "phone", "email", "amount", "payment_method"):
        assert ic[k]["status"] == "not_provided", k


async def test_old_names_still_work_and_conflicts_are_refused(app_cl, tok):
    _, cl = app_cl
    r = await cl.post(
        "/analyze", json={"text": PHISH, "suspicious_url": "https://bit.ly/x"}, headers=tok("a")
    )
    assert r.status_code == 200
    assert r.json()["input_capabilities"]["url"]["status"] == "analysed"
    r = await cl.post(
        "/analyze",
        json={"text": PHISH, "url": "https://bit.ly/x", "suspicious_url": "https://evil.top/q"},
        headers=tok("a"),
    )
    assert r.status_code == 422 and "evil.top" not in r.text
    assert r.json()["detail"][0]["loc"] == ["body", "url"]


@pytest.mark.parametrize(
    "extra",
    [
        {"url": "u" * 2049},
        {"phone": "9" * 41},
        {"email": "e" * 255},
        {"amount": "-5"},
        {"amount": "abc"},
        {"payment_method": "BITCOIN"},
        {"upi_id": "x" * 321},
    ],
)
async def test_validation_without_echo(app_cl, tok, extra):
    _, cl = app_cl
    r = await cl.post("/analyze", json={"text": PHISH, **extra}, headers=tok("a"))
    assert r.status_code == 422
    for v in extra.values():
        assert str(v)[:20] not in r.text or str(v) in ("UPI",)


async def test_short_text_is_insufficient_not_low(app_cl, tok):
    _, cl = app_cl
    a = (await cl.post("/analyze", json={"text": "hello there"}, headers=tok("a"))).json()
    assert a["status"] == "INSUFFICIENT_INPUT" and a["risk_level"] is None
    assert a["input_capabilities"]["text"]["status"] == "insufficient_input"


async def test_text_spans_point_to_the_text_field(make_app, tok):
    pytest.importorskip("call_guard")
    from complaints.analysis import ComplaintAnalysisPipeline

    app = make_app(analysis=ComplaintAnalysisPipeline(category_loader=lambda: None))
    cl = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    msg = "Hello sir. Transfer your money to the RBI safe account for verification now."
    a = (await cl.post("/analyze", json={"text": msg}, headers=tok("a"))).json()
    rules = [i for i in a["indicators"] if i["source"] == "rule"]
    assert rules and all(i["evidence"]["field"] == "text" for i in rules)
    ev = rules[0]["evidence"]
    assert msg[ev["start"] : ev["end"]].startswith("Transfer your money")
