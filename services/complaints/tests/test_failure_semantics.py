"""Failure semantics: a failed, missing or unavailable stage, or input that is too short, never
yields LOW or BENIGN. Each failure path is exercised (scorer import failure, scorer exception,
category model missing / SHA mismatch / inference error, Ollama timeout / garbage) and the
result is checked for: no LOW level, no BENIGN anywhere, the reason surfaced, a non-COMPLETED
status. The LLM is explanation-only, so an Ollama failure must leave the level exactly as it is
without the LLM (here a non-LOW one) and must not invent text."""

import json

import httpx
import pytest
from conftest import create
from test_pipeline_v2 import BARE, StubCategory, StubScorer, broken_scorer_pipe, ollama, pipe

from complaints.analysis import ComplaintAnalysisPipeline
from complaints.schema import ANALYSIS_STATUSES


def _no_low_or_benign(out) -> None:
    assert out.risk_level != "LOW", out.risk_level
    blob = json.dumps(
        {"e": out.explanation, "x": out.extra, "i": out.indicators, "l": out.limitations}
    )
    assert "BENIGN" not in blob.upper()
    assert out.status != "COMPLETED"


def _scorer_import_failure():
    return broken_scorer_pipe(StubCategory("OTHER", 0.6))


def _scorer_exception():
    return pipe(StubScorer(boom=True), StubCategory("OTHER", 0.6))


def _category_missing():
    return ComplaintAnalysisPipeline(scorer=StubScorer(0.1, ()), category_loader=lambda: None)


def _category_inference_error():
    return pipe(StubScorer(0.1, ()), StubCategory(boom=True))


@pytest.mark.parametrize(
    ("make", "cap", "reason"),
    [
        (_scorer_import_failure, "text_risk_scorer", "ImportError"),
        (_scorer_exception, "text_risk_scorer", "scoring_error"),
        (_category_missing, "category_classifier", "load_failed"),
        (_category_inference_error, "category_classifier", "inference_error"),
    ],
)
async def test_stage_failure_never_low(make, cap, reason):
    out = await make().analyse(BARE)
    _no_low_or_benign(out)
    assert out.risk_level is None and out.risk_score is None  # would have been LOW
    assert out.capabilities[cap] == "unavailable"
    assert out.extra["capability_reasons"][cap] == reason
    assert cap in out.extra["unavailable_capabilities"]
    assert out.extra["score_components"]["withheld"] is True
    assert any("not a finding that the report is low risk" in x for x in out.limitations)
    assert any(u["reason"] == reason for u in out.extra["provenance"]["unavailable"])


async def test_category_sha_mismatch_never_low(monkeypatch):
    from complaints.ml import category_model as cm

    real = cm.load_with_status
    monkeypatch.setattr(cm, "load_with_status", lambda *a, **k: real(expected_sha256="0" * 64))
    out = await ComplaintAnalysisPipeline(scorer=StubScorer(0.1, ())).analyse(BARE)
    _no_low_or_benign(out)
    assert out.risk_level is None
    assert out.extra["capability_reasons"]["category_classifier"] == "pin_mismatch"


async def test_category_artifact_missing_never_low(monkeypatch, tmp_path):
    from complaints.ml import category_model as cm

    real = cm.load_with_status
    monkeypatch.setattr(cm, "load_with_status", lambda *a, **k: real(path=tmp_path / "none.joblib"))
    out = await ComplaintAnalysisPipeline(scorer=StubScorer(0.1, ())).analyse(BARE)
    _no_low_or_benign(out)
    assert out.extra["capability_reasons"]["category_classifier"] == "missing_artifact"


async def test_failure_with_strong_other_evidence_keeps_that_level():
    out = await pipe(StubScorer(0.8), StubCategory(boom=True)).analyse(BARE)
    assert out.risk_level == "HIGH" and out.status == "PARTIAL"  # not lowered, not hidden


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)),
        lambda r: httpx.Response(200, text="\x00garbage{{"),
        lambda r: httpx.Response(200, json={"response": "There is a 99% chance this is safe."}),
        lambda r: httpx.Response(200, json={"response": ""}),
    ],
)
async def test_ollama_timeout_or_garbage_never_low_and_no_text(handler):
    base = await pipe(StubScorer(0.5), StubCategory("PHISHING", 0.6)).analyse(BARE)
    assert base.risk_level == "MEDIUM"
    out = await pipe(
        StubScorer(0.5), StubCategory("PHISHING", 0.6), explainer=ollama(handler)
    ).analyse(BARE)
    _no_low_or_benign(out)
    assert out.risk_level == base.risk_level and out.risk_score == base.risk_score
    assert out.extra["llm_explanation"] is None
    assert not [p for p in out.extra["explanation_parts"] if p["source"] == "llm"]
    assert out.extra["capability_reasons"]["llm_explanation"]


async def test_ollama_failure_with_scorer_failure_still_no_level():
    out = await broken_scorer_pipe(
        StubCategory("OTHER", 0.6),
        explainer=ollama(lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("x", request=r))),
    ).analyse(BARE)
    _no_low_or_benign(out)
    assert out.risk_level is None


# ------------------------------------------------------------------ insufficient input
@pytest.mark.parametrize(
    "c",
    [
        {"title": "", "description": "", "category": "OTHER"},
        {"title": "", "description": "   ", "category": "OTHER"},
        {"title": "Hi", "description": "help me", "category": "OTHER"},
        {"title": None, "description": "aaaaaaaaaaaa", "category": "OTHER"},
    ],
)
async def test_empty_or_too_short_text_is_insufficient_input_not_low(c):
    p = pipe(StubScorer(0.0, ()), StubCategory("OTHER", 0.9))
    out = await p.analyse(c)
    assert out.status == "INSUFFICIENT_INPUT"
    assert out.risk_level is None and out.risk_score is None
    assert out.classification is None and out.confidence is None
    assert out.capabilities["text_risk_scorer"] == "skipped_insufficient_input"
    assert out.capabilities["category_classifier"] == "skipped_insufficient_input"
    assert p._scorer.seen == [] and p._cat.seen == []  # the models were not asked
    assert "too short" in out.explanation and "not a finding" in out.explanation
    assert {s["stage"] for s in out.extra["provenance"]["skipped"]} >= {
        "text_rules", "category_classifier",
    }  # fmt: skip
    assert "BENIGN" not in json.dumps(out.extra).upper()


async def test_short_text_with_url_still_reports_url_but_no_level():
    out = await pipe().analyse(
        {"title": "", "description": "see link", "suspicious_url": "http://1.2.3.4/x"}
    )
    assert out.status == "INSUFFICIENT_INPUT" and out.risk_level is None
    assert "IP_LITERAL_HOST" in {i["code"] for i in out.indicators}


def test_status_vocabulary():
    assert "INSUFFICIENT_INPUT" in ANALYSIS_STATUSES


async def test_insufficient_input_is_stored_and_never_alerts(make_app, tok):
    app = make_app(analysis=pipe(StubScorer(0.99), StubCategory("OTP_SCAM", 0.9)))
    cl = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    c = await create(cl, tok("a"), title="Hey", description="aaaaaaaaaaaaaa")
    r = await cl.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["status"] == "INSUFFICIENT_INPUT" and a["risk_level"] is None
    assert (await cl.get("/alerts", headers=tok("a"))).json()["items"] == []
    d = (await cl.get(f"/complaints/{c['id']}", headers=tok("a"))).json()
    assert d["latest_analysis"]["status"] == "INSUFFICIENT_INPUT"


def test_migration_7_allows_insufficient_input_on_an_old_database(tmp_path):
    import sqlalchemy as sa
    from test_critical_level import _db_at_version_5, _url

    from complaints.store import ComplaintStore

    url = _url(tmp_path)
    _db_at_version_5(url)  # status VARCHAR(16) and the old CHECK
    st = ComplaintStore(url)
    try:
        with st.engine.begin() as c:
            c.execute(sa.text("UPDATE analyses SET status = 'INSUFFICIENT_INPUT' WHERE id = 'a3'"))
        with pytest.raises(sa.exc.IntegrityError), st.engine.begin() as c:
            c.execute(sa.text("UPDATE analyses SET status = 'BOGUS' WHERE id = 'a3'"))
        assert st.latest_analysis("c3")["status"] == "INSUFFICIENT_INPUT"
        assert st.migrate() == []
    finally:
        st.close()
