"""Analysis: honest text-only pipeline, versions, unavailable scorer, static guidance."""

import json

import pytest
from conftest import MIN, create

from complaints.analysis import (
    AnalysisOutcome,
    CallGuardTextAnalysis,
    band,
    static_guidance,
)

SCAM = (
    "A man called saying he is from CBI. He said: You are under digital arrest. Do not "
    "disconnect this video call. Transfer your money to the RBI safe account for verification "
    "or you will be arrested."
)


class StubScorer:
    model_version = "stub-1"

    def __init__(self, score=0.9, reasons=None, boom=False):
        self.score, self.boom = score, boom
        self.reasons = reasons or [("DIGITAL_ARREST_PHRASE", 0.69, "caller says digital arrest")]
        self.seen: list[str] = []

    def score_text(self, text):
        self.seen.append(text)
        if self.boom:
            raise RuntimeError("model exploded")
        from scam_contracts.models import Reason

        return (
            self.score,
            [Reason(code=c, weight=w, detail=d) for c, w, d in self.reasons],
            "stub-1",
        )


def test_bands():
    assert band(0) == "LOW" and band(34) == "LOW"
    assert band(35) == "MEDIUM" and band(69) == "MEDIUM"
    assert band(70) == "HIGH" and band(100) == "HIGH"


async def test_text_only_complaint_is_analysable(client, tok, make_app):
    from conftest import bearer  # noqa: F401

    c = await create(client, tok("a"), category="DIGITAL_ARREST", description=SCAM)
    r = await client.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))
    assert r.status_code == 201
    a = r.json()
    assert a["version"] == 1 and a["complaint_id"] == c["id"]
    assert a["score_semantics"] == "uncalibrated_heuristic"
    assert a["classification"] is None and a["confidence"] is None and a["explanation"] is None
    assert a["capabilities"]["category_classifier"] == "unavailable"
    assert a["capabilities"]["llm_explanation"] == "unavailable"


async def test_real_call_guard_scorer_if_installed():
    pytest.importorskip("call_guard")
    svc = CallGuardTextAnalysis()
    complaint = {"title": "Digital arrest call", "description": SCAM, "category": "DIGITAL_ARREST",
                 "amount": None, "suspect_phone": None, "suspicious_url": None, "city": None}  # fmt: skip
    out = await svc.analyse(complaint)
    assert out.status in ("COMPLETED", "PARTIAL")
    assert out.risk_score is not None and out.risk_score >= 70 and out.risk_level == "HIGH"
    assert out.indicators and all(i["text"] for i in out.indicators)
    assert out.capabilities["text_risk_scorer"] == "available"
    assert out.classification is None
    benign = dict(
        complaint,
        title="Lost parcel",
        description="My parcel arrived late and the box was damaged, please help.",
    )
    out2 = await svc.analyse(benign)
    assert out2.risk_level == "LOW"


async def test_stub_mapping_scores_levels_and_indicators():
    svc = CallGuardTextAnalysis(scorer=StubScorer(0.8))
    out = await svc.analyse({"title": "t", "description": "x" * 20, "category": "OTP_SCAM"})
    assert out.risk_score == 80 and out.risk_level == "HIGH" and out.status == "COMPLETED"
    assert out.indicators[0]["code"] == "DIGITAL_ARREST_PHRASE"
    assert out.indicators[0]["source"] == "call_guard" and out.indicators[0]["text"]
    assert any(a["source"] == "static_guidance" for a in out.recommended_actions)
    assert any("uncalibrated" in lim.lower() for lim in out.limitations)
    assert out.pipeline_version.endswith("stub-1")


async def test_no_risk_reason_is_not_listed_as_indicator():
    from scam_contracts.models import Reason  # noqa: F401

    svc = CallGuardTextAnalysis(scorer=StubScorer(0.0, [("NO_RISK_INDICATORS", 0.0, "none")]))
    out = await svc.analyse({"title": "t", "description": "y" * 20, "category": "OTHER"})
    assert out.risk_score == 0 and out.risk_level == "LOW" and out.indicators == []


async def test_scorer_failure_is_unavailable_never_low():
    svc = CallGuardTextAnalysis(scorer=StubScorer(boom=True))
    out = await svc.analyse({"title": "t", "description": "z" * 20, "category": "OTHER"})
    assert out.status == "UNAVAILABLE" and out.risk_score is None and out.risk_level is None
    assert out.capabilities["text_risk_scorer"] == "unavailable"
    assert out.recommended_actions  # static guidance still offered, labelled as such


async def test_scorer_import_missing_is_unavailable(monkeypatch):
    def loader():
        raise ImportError("no call_guard")

    svc = CallGuardTextAnalysis(scorer_loader=loader)
    out = await svc.analyse({"title": "t", "description": "z" * 20, "category": "OTHER"})
    assert out.status == "UNAVAILABLE" and out.risk_level is None


async def test_missing_optional_fields_never_block_or_count_as_evidence():
    s = StubScorer(0.1, [("NO_RISK_INDICATORS", 0.0, "none")])
    svc = CallGuardTextAnalysis(scorer=s)
    bare = {"title": "t", "description": "w" * 20, "category": "UPI_FRAUD"}
    full = dict(
        bare, amount="10", suspect_phone="+919876543210", suspicious_url="https://x.example"
    )
    a, b = await svc.analyse(bare), await svc.analyse(full)
    assert a.status == "COMPLETED" and a.risk_score == b.risk_score
    assert s.seen[0] == s.seen[1]  # only the text is scored


async def test_versions_persist_and_latest_in_detail(client, tok, analysis):
    c = await create(client, tok("a"))
    r1 = await client.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))
    r2 = await client.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))
    assert (r1.json()["version"], r2.json()["version"]) == (1, 2)
    g = (await client.get(f"/complaints/{c['id']}/analysis", headers=tok("a"))).json()
    assert g["version"] == 2 and [h["version"] for h in g["history"]] == [2, 1]
    d = (await client.get(f"/complaints/{c['id']}", headers=tok("a"))).json()
    assert d["latest_analysis"]["version"] == 2 and d["latest_analysis"]["status"] == "COMPLETED"
    assert len(analysis.calls) == 2


async def test_no_analysis_yet_is_404_code(client, tok):
    c = await create(client, tok("a"))
    r = await client.get(f"/complaints/{c['id']}/analysis", headers=tok("a"))
    assert r.status_code == 404 and r.json()["error"] == "analysis_not_found"
    d = (await client.get(f"/complaints/{c['id']}", headers=tok("a"))).json()
    assert d["latest_analysis"] is None


async def test_analysis_authz(client, tok, inv):
    c = await create(client, tok("a"))
    assert (
        await client.post(f"/complaints/{c['id']}/analysis", headers=tok("b"))
    ).status_code == 404
    assert (await client.post(f"/complaints/{c['id']}/analysis", headers=inv)).status_code == 201
    assert (
        await client.get(f"/complaints/{c['id']}/analysis", headers=tok("b"))
    ).status_code == 404


async def test_high_risk_alerts_owner_once_per_version(client, tok, inv, analysis):
    rule = {"code": "DIGITAL_ARREST_PHRASE", "source": "rule", "weight": 0.69, "text": "x"}
    analysis.outcome = AnalysisOutcome(
        status="COMPLETED", pipeline_version="t", capabilities={}, risk_score=88, risk_level="HIGH",
        indicators=[rule], recommended_actions=[], limitations=[],
    )  # fmt: skip
    c = await create(client, tok("a"))
    await client.post(f"/complaints/{c['id']}/analysis", headers=inv)  # run by investigator
    alerts = (await client.get("/alerts", headers=tok("a"))).json()["items"]
    assert [a["kind"] for a in alerts] == ["HIGH_RISK_ANALYSIS"]
    assert (await client.get("/alerts", headers=inv)).json()["items"] == []
    low = analysis.outcome.__class__(
        **{**analysis.outcome.__dict__, "risk_level": "LOW", "risk_score": 5}
    )
    analysis.outcome = low
    await client.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))
    assert len((await client.get("/alerts", headers=tok("a"))).json()["items"]) == 1


async def test_no_alert_for_high_level_without_a_rule_based_indicator(client, tok, analysis):
    # a level that rests on a model output alone must not alert the citizen (audit 2026-10-17)
    ml_only = {"code": "SCRIPT_CLASSIFIER_MATCH", "source": "ml", "weight": 0.9, "text": "x"}
    for inds in ([], [ml_only]):
        analysis.outcome = AnalysisOutcome(
            status="COMPLETED", pipeline_version="t", capabilities={}, risk_score=80,
            risk_level="HIGH", indicators=inds, recommended_actions=[], limitations=[],
        )  # fmt: skip
        c = await create(client, tok("a"))
        r = await client.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))
        assert r.status_code == 201
    assert (await client.get("/alerts", headers=tok("a"))).json()["items"] == []


async def test_unavailable_analysis_persists_without_level(client, tok, analysis):
    analysis.outcome = AnalysisOutcome(
        status="UNAVAILABLE", pipeline_version="t", capabilities={"text_risk_scorer": "unavailable"},
        risk_score=None, risk_level=None, indicators=[], recommended_actions=[],
        limitations=["scorer unavailable"],
    )  # fmt: skip
    c = await create(client, tok("a"))
    r = await client.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))
    assert r.status_code == 201 and r.json()["status"] == "UNAVAILABLE"
    assert r.json()["risk_score"] is None and r.json()["risk_level"] is None


async def test_payload_never_claims_probability(client, tok):
    c = await create(client, tok("a"), **{"category": "DIGITAL_ARREST", "description": SCAM})
    r = await client.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))
    blob = json.dumps(r.json()).casefold()
    assert "probab" not in blob and "calibrated_probability" not in blob
    assert r.json()["score_semantics"] == "uncalibrated_heuristic"


def test_static_guidance_is_labelled_and_keyed():
    for cat in ("DIGITAL_ARREST", "UPI_FRAUD", "OTP_SCAM", "OTHER", "PHISHING"):
        for level in ("LOW", "MEDIUM", "HIGH", None):
            acts = static_guidance(cat, level)
            assert acts and all(a["source"] == "static_guidance" and a["text"] for a in acts)
    assert any("1930" in a["text"] for a in static_guidance("UPI_FRAUD", "HIGH"))
    assert MIN  # used import


async def test_older_analyses_get_an_assessment_derived_from_stored_evidence(client, tok, analysis):
    # review 2026-10-18: analyses stored before the assessment existed (for example a HIGH that
    # rested on the removed category prior, with no indicator) must not read "strong indicators"
    cases = [
        ([], "no_indicators_found"),
        ([{"code": "SCRIPT_CLASSIFIER_MATCH", "source": "ml", "method": "call_guard.clf-v1",
           "weight": 0.8, "text": "x"}], "model_prediction_only"),
        ([{"code": "DIGITAL_ARREST_PHRASE", "source": "rule", "method": "call_guard.rules",
           "weight": 0.7, "text": "x"}], "indicators_found"),
        # a pre-provenance row (old source vocabulary, no method)
        ([{"code": "DIGITAL_ARREST_PHRASE", "source": "call_guard", "weight": 0.7, "text": "x"}],
         "indicators_found"),
    ]  # fmt: skip
    for inds, expected in cases:
        analysis.outcome = AnalysisOutcome(  # no extra["assessment"]: stored like an older row
            status="COMPLETED", pipeline_version="t", capabilities={}, risk_score=70,
            risk_level="HIGH", indicators=inds, recommended_actions=[], limitations=[],
        )  # fmt: skip
        c = await create(client, tok("a"))
        await client.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))
        full = (await client.get(f"/complaints/{c['id']}/analysis", headers=tok("a"))).json()
        assert (full["assessment"], full["assessment_derived"]) == (expected, True)
        summary = (await client.get(f"/complaints/{c['id']}", headers=tok("a"))).json()
        latest = summary["latest_analysis"]
        assert (latest["assessment"], latest["assessment_derived"]) == (expected, True)
