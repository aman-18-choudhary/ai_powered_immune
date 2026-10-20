"""Provenance (source/method/evidence on every indicator, explanation part and action, plus the
analysis-level summary) and the confidence interpretation. Real call-guard rules where noted."""

import datetime as dt
import json

import httpx
import pytest
from call_guard.rules import RULES_VERSION
from conftest import create
from test_pipeline_v2 import (
    BARE,
    StubCategory,
    StubScorer,
    broken_scorer_pipe,
    ok_handler,
    ollama,
    pipe,
)

from complaints.analysis import ComplaintAnalysisPipeline
from complaints.provenance import SOURCES, TextLayout, legacy_indicator, legacy_provenance

LONG = (
    "Yesterday evening I received a call on my mobile from a man who said he was an officer. "
    "Do not disconnect this video call. "
    "Transfer your money to the RBI safe account for verification or you will be arrested. "
    "I was scared and stayed on the call for two hours before my son came home."
)


def _all_items(out) -> list[dict]:
    return [*out.indicators, *out.recommended_actions, *out.extra["explanation_parts"]]


def _check_vocab(items: list[dict]) -> None:
    for it in items:
        assert it["source"] in SOURCES, it
        assert isinstance(it["method"], str) and it["method"], it


async def test_every_item_has_source_method_and_evidence_stub():
    reasons = (("URGENCY", 0.15, "creates urgency"),
               ("SCRIPT_CLASSIFIER_MATCH", 0.8, "text matches known scripts"))  # fmt: skip
    out = await pipe(StubScorer(0.8, reasons), StubCategory("OTP_SCAM", 0.6)).analyse(
        {**BARE, "suspicious_url": "http://sbi-kyc.example.top/login", "upi_id": "x@okaxis"}
    )
    _check_vocab(_all_items(out))
    by_code = {i["code"]: i for i in out.indicators}
    ml = by_code["SCRIPT_CLASSIFIER_MATCH"]
    assert ml["source"] == "ml" and ml["method"] == "call_guard.stub-1"
    assert ml["evidence"]["type"] == "whole_text" and ml["stage"] == "call_guard"
    assert by_code["URGENCY"]["source"] == "rule"
    assert by_code["URGENCY"]["method"] == "call_guard.rules"
    url = [i for i in out.indicators if i["stage"] == "url_heuristics"]
    assert url and all(i["source"] == "rule" and i["method"] == "url_heuristics" for i in url)
    assert url[0]["evidence"] == {"type": "field", "field": "suspicious_url",
                                  "host": "sbi-kyc.example.top"}  # fmt: skip
    for a in out.recommended_actions:
        assert a["source"] == "static_guidance" and a["method"] == "static_guidance_table"
        assert a["evidence"]["type"] == "keyed_by"
    checks = out.extra["entity_checks"]["checks"]
    assert checks and all(c["source"] == "rule" and c["method"] == "entity_checks" for c in checks)
    assert checks[0]["evidence"] == {"type": "field", "field": "upi_id"}
    parts = out.extra["explanation_parts"]
    assert out.explanation == " ".join(p["text"] for p in parts)
    assert {p["source"] for p in parts} >= {"rule", "ml"}
    assert any(p["method"] == "category-stub" for p in parts)  # the category sentence


async def test_real_rule_indicators_point_to_a_sentence_not_the_whole_text():
    pytest.importorskip("call_guard")
    c = {"title": "Digital arrest call", "description": LONG, "category": "DIGITAL_ARREST"}
    out = await ComplaintAnalysisPipeline(category_loader=lambda: None).analyse(c)
    rules = [i for i in out.indicators if i["source"] == "rule"]
    assert rules, out.indicators
    for i in rules:
        ev = i["evidence"]
        assert ev["type"] == "text_span" and ev["granularity"] == "sentence"
        assert ev["field"] in ("title", "description")
        src = c[ev["field"]]
        assert 0 <= ev["start"] < ev["end"] <= len(src)
        assert " ".join(src[ev["start"] : ev["end"]].split()).startswith(ev["snippet"][:40])
        assert len(ev["snippet"]) <= 160
    blob = json.dumps(out.indicators)
    assert LONG not in blob  # the whole description is never copied into evidence
    assert any("safe account" in i["evidence"]["snippet"].lower() for i in rules)
    starts = {i["evidence"]["start"] for i in rules if i["stage"] == "call_guard"}
    assert len(starts) == 2  # two different sentences carry the call-script cues
    narr = [i for i in rules if i["stage"] == "complaint_narrative"]
    assert [i["code"] for i in narr] == ["CLAIMED_OFFICIAL_IDENTITY"]  # "said he was an officer"
    assert narr[0]["evidence"]["start"] == 0 and narr[0]["method"] == "complaints.narrative"
    prov = out.extra["provenance"]
    assert "text_rules" in prov["ran"]
    assert prov["model_versions"]["call_guard_rules"] == RULES_VERSION
    assert any(u["stage"] == "category_classifier" for u in prov["unavailable"])


def test_text_layout_offsets_map_to_fields():
    lay = TextLayout("Title here", "First one. Second sentence here.", 4000)
    spans = lay.sentences()
    evs = [lay.span_evidence(a, b) for a, b in spans]
    assert [e["field"] for e in evs] == ["title", "description", "description"]
    assert evs[2]["start"] == len("First one. ") and evs[2]["snippet"] == "Second sentence here."
    assert TextLayout(None, "only text", 4000).field_label == "description"


async def test_provenance_summary_stages_and_versions():
    out = await pipe(StubScorer(0.5), StubCategory("PHISHING", 0.6)).analyse(BARE)
    prov = out.extra["provenance"]
    assert prov["derived_from_legacy"] is False and prov["legacy"] is False
    assert set(prov["sources"]) == set(SOURCES)
    assert {"text_rules", "script_classifier", "category_classifier", "static_guidance"} <= set(
        prov["ran"]
    )
    assert {s["stage"] for s in prov["skipped"]} >= {"url_heuristics", "entity_checks"}
    llm = next(s for s in prov["stages"] if s["stage"] == "llm_explanation")
    assert llm["status"] == "not_configured" and llm["source"] == "llm"
    assert prov["model_versions"]["category"] == "category-stub"
    assert prov["model_versions"]["call_guard_classifier"] == "stub-1"
    for st in prov["stages"]:
        assert st["source"] in SOURCES and st["method"]


async def test_provenance_names_unavailable_scorer_with_reason():
    out = await broken_scorer_pipe(StubCategory("OTHER", 0.6)).analyse(BARE)
    un = {u["stage"]: u["reason"] for u in out.extra["provenance"]["unavailable"]}
    assert un["text_rules"] == "ImportError" and un["script_classifier"] == "ImportError"
    assert out.extra["provenance"]["model_versions"]["call_guard_rules"] is None


async def test_rules_only_fallback_reports_classifier_unavailable():
    class RulesOnly(StubScorer):
        fallback_mode = True

        def score_text(self, text):
            s, r, _ = super().score_text(text)
            return s, r, RULES_VERSION

    out = await pipe(RulesOnly(0.5)).analyse(BARE)
    st = {s["stage"]: s for s in out.extra["provenance"]["stages"]}
    assert st["text_rules"]["status"] == "ran"
    assert st["script_classifier"]["status"] == "unavailable"
    assert st["script_classifier"]["reason"] == "fallback_rules_only"


async def test_llm_text_is_a_separate_llm_part():
    cap: list = []
    out = await pipe(StubScorer(0.8), explainer=ollama(ok_handler(cap))).analyse(BARE)
    llm = [p for p in out.extra["explanation_parts"] if p["source"] == "llm"]
    assert len(llm) == 1 and llm[0]["method"] == "ollama:qwen2.5:3b"
    assert llm[0]["evidence"]["changes_score"] is False
    assert llm[0]["text"] not in out.explanation  # the rule text stays rule-only
    assert "llm_explanation" in out.extra["provenance"]["ran"]
    assert out.extra["provenance"]["model_versions"]["llm"] == "qwen2.5:3b"


# ------------------------------------------------------------------ confidence
async def test_confidence_interpretation_answered():
    out = await pipe(StubScorer(0.5), StubCategory("PHISHING", 0.62)).analyse(BARE)
    ci = out.extra["confidence_interpretation"]
    assert "0.62" in ci and "synthetic" in ci and "uncalibrated" in ci
    assert "probab" not in ci.casefold()
    cm = out.extra["category_model"]
    assert cm["abstention_threshold"] == 0.25 and len(cm["top_categories"]) <= 3
    assert cm["source"] == "ml" and cm["method"] == "category-stub"
    assert isinstance(out.confidence, float)


async def test_confidence_interpretation_abstained_and_unavailable():
    ab = await pipe(StubScorer(0.5), StubCategory("UPI_FRAUD", 0.12)).analyse(BARE)
    ci = ab.extra["confidence_interpretation"]
    assert ab.classification == "UNDETERMINED" and "UNDETERMINED" in ci and "0.25" in ci
    none = await ComplaintAnalysisPipeline(
        scorer=StubScorer(0.5), category_loader=lambda: None
    ).analyse(BARE)
    assert none.confidence is None
    assert none.extra["confidence_interpretation"].startswith("No confidence is given")


async def test_real_category_top3():
    pytest.importorskip("sklearn")
    out = await ComplaintAnalysisPipeline(scorer=StubScorer(0.4)).analyse(BARE)
    cm = out.extra["category_model"]
    assert len(cm["top_categories"]) == 3
    scores = [t["score"] for t in cm["top_categories"]]
    assert scores == sorted(scores, reverse=True)


# ------------------------------------------------------------------ API and legacy rows
@pytest.fixture
def pclient(make_app):
    app = make_app(analysis=pipe(StubScorer(0.8), StubCategory("OTP_SCAM", 0.6)))
    return app, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_api_exposes_provenance_parts_and_interpretation(pclient, tok):
    _, cl = pclient
    c = await create(cl, tok("a"), suspicious_url="https://bit.ly/x")
    a = (await cl.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))).json()
    for k in ("provenance", "explanation_parts", "confidence_interpretation", "llm_model"):
        assert k in a, k
    assert a["provenance"]["derived_from_legacy"] is False
    _check_vocab([*a["indicators"], *a["recommended_actions"], *a["explanation_parts"]])
    g = (await cl.get(f"/complaints/{c['id']}/analysis", headers=tok("a"))).json()
    assert g["provenance"] == a["provenance"]
    r = (await cl.post("/analyze", json={"text": BARE["description"]}, headers=tok("a"))).json()
    assert r["provenance"]["ran"] and r["confidence_interpretation"]
    _check_vocab([*r["indicators"], *r["recommended_actions"], *r["explanation_parts"]])


def _insert(app, cid, **over):
    from complaints.schema import analyses

    vals = dict(
        id=over.pop("id", "legacy-1"), complaint_id=cid, version=over.pop("version", 1),
        created_at=dt.datetime.now(dt.UTC), pipeline_version="complaints-text-v1+clf-v1",
        capabilities={"text_risk_scorer": "available", "category_classifier": "unavailable",
                      "llm_explanation": "unavailable"},
        risk_score=80, risk_level="HIGH", classification=None, confidence=None,
        indicators=[{"code": "DIGITAL_ARREST_PHRASE", "weight": 0.69, "text": "x",
                     "source": "call_guard"},
                    {"code": "SCRIPT_CLASSIFIER_MATCH", "weight": 0.9, "text": "y",
                     "source": "call_guard"}],
        explanation=None, recommended_actions=[{"text": "t", "source": "static_guidance"}],
        limitations=["old"], status="COMPLETED",
    )  # fmt: skip
    vals.update(over)
    with app.state.store.engine.begin() as conn:
        conn.execute(analyses.insert().values(**vals))


async def test_legacy_v1_row_gets_derived_provenance(pclient, tok):
    app, cl = pclient
    c = await create(cl, tok("a"))
    _insert(app, c["id"])
    g = (await cl.get(f"/complaints/{c['id']}/analysis", headers=tok("a"))).json()
    p = g["provenance"]
    assert p["derived_from_legacy"] is True and p["legacy"] is False
    assert p["ran"][:2] == ["text_rules", "script_classifier"]
    assert p["model_versions"]["call_guard"] == "clf-v1"
    ind = {i["code"]: i for i in g["indicators"]}
    assert ind["DIGITAL_ARREST_PHRASE"]["source"] == "rule"
    assert ind["DIGITAL_ARREST_PHRASE"]["stage"] == "call_guard"
    assert ind["SCRIPT_CLASSIFIER_MATCH"]["source"] == "ml"
    assert ind["SCRIPT_CLASSIFIER_MATCH"]["evidence"] == {"type": "not_recorded"}
    assert g["recommended_actions"][0]["method"] == "static_guidance_table"
    assert g["explanation_parts"] == [] and g["confidence_interpretation"] is None


def test_unknown_pipeline_is_marked_legacy():
    p = legacy_provenance({"pipeline_version": "something-else", "capabilities": {}})
    assert p["legacy"] is True and p["derived_from_legacy"] is True
    i = legacy_indicator({"code": "X", "weight": 0.1, "text": "x", "source": "mystery"})
    assert i["source"] == "legacy" and i["method"] == "unknown"
