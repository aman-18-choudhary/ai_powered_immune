"""Analysis pipeline v2 and POST /analyze: stages, honest degradation, Ollama explainer with a
fake transport, API additivity. Real call-guard and the real category artifact are used where
noted; otherwise stubs."""

import json
import logging

import httpx
import pytest
from conftest import MP3, PNG, bearer, create
from sqlalchemy import text as sql_text

from complaints.analysis import (
    CAPABILITIES_NONE,
    AnalysisOutcome,
    ComplaintAnalysisPipeline,
    band_v2,
    stored_level,
)
from complaints.explain import (
    MAX_LLM_OUTPUT,
    OllamaClient,
    build_prompt,
    sanitise,
    validate_output,
)
from complaints.ml.category_model import CategoryPrediction

PHISH = (
    "I got an SMS saying my bank KYC will expire today and to update it at the link; the page "
    "asked for my card number and password."
)
BARE = {"title": "Report", "description": PHISH, "category": "PHISHING"}


class StubScorer:
    model_version = "stub-1"

    def __init__(self, score=0.5, reasons=(("URGENCY", 0.3, "creates urgency"),), boom=False):
        self.score, self.reasons, self.boom = score, reasons, boom
        self.seen: list[str] = []

    def score_text(self, text):
        self.seen.append(text)
        if self.boom:
            raise RuntimeError("boom")
        from scam_contracts.models import Reason

        return (
            self.score,
            [Reason(code=c, weight=w, detail=d) for c, w, d in self.reasons],
            "stub-1",
        )


class StubCategory:
    version = "category-stub"
    min_confidence = 0.25

    def __init__(self, label="PHISHING", conf=0.6, boom=False):
        self.label, self.conf, self.boom = label, conf, boom
        self.seen: list[str] = []

    def predict(self, text):
        self.seen.append(text)
        if self.boom:
            raise RuntimeError("boom")
        ab = self.conf < self.min_confidence
        return CategoryPrediction(
            "UNDETERMINED" if ab else self.label,
            self.conf,
            ((self.label, self.conf),),
            ab,
            self.label,
        )


def pipe(scorer=None, cat=None, explainer=None, **kw):
    return ComplaintAnalysisPipeline(
        scorer=scorer or StubScorer(),
        category_model=cat or StubCategory(),
        explainer=explainer,
        **kw,
    )


def broken_scorer_pipe(cat=None, explainer=None):
    def loader():
        raise ImportError("nope")

    return ComplaintAnalysisPipeline(
        scorer_loader=loader, category_model=cat or StubCategory(), explainer=explainer
    )


# ------------------------------------------------------------------ real components
async def test_real_pipeline_text_only_complaint_is_analysable():
    pytest.importorskip("call_guard")
    out = await ComplaintAnalysisPipeline().analyse(BARE)
    assert out.pipeline_version.startswith("complaints-analysis-v2+")
    assert out.status in ("COMPLETED", "PARTIAL")
    assert out.classification == "PHISHING" or out.classification == "UNDETERMINED"
    assert out.capabilities["text_risk_scorer"] == "available"
    assert out.capabilities["category_classifier"] == "available"
    assert out.confidence is not None and out.extra["confidence_semantics"] == (
        "model_confidence_uncalibrated"
    )
    assert out.explanation and out.extra["explanation_source"] == "rules"
    assert out.extra["category_model"]["trained_on"] == "synthetic data only"
    assert "probab" not in json.dumps(out.extra | {"e": out.explanation}).casefold()


async def test_real_pipeline_benign_text_is_low_or_other():
    pytest.importorskip("call_guard")
    out = await ComplaintAnalysisPipeline().analyse(
        {
            "title": "Lost parcel",
            "category": "OTHER",
            "description": "My parcel arrived late and the box was damaged, please help me.",
        }  # fmt: skip
    )
    assert out.risk_level == "LOW" and out.status in ("COMPLETED", "PARTIAL")


# ------------------------------------------------------------------ formula and bands
def test_bands_v2_and_stored_level_mapping():
    assert [band_v2(x) for x in (0, 34, 35, 69, 70, 89, 90, 100)] == [
        "LOW", "LOW", "MEDIUM", "MEDIUM", "HIGH", "HIGH", "CRITICAL", "CRITICAL",
    ]  # fmt: skip
    # since migration 6 the exact level is stored (CRITICAL was stored as HIGH before it)
    assert stored_level("CRITICAL") == "CRITICAL" and stored_level("LOW") == "LOW"
    assert stored_level(None) is None


async def test_score_is_evidence_only_category_guess_not_added_plus_url_adjustment():
    # accuracy audit 2026-10-17: the category guess used to raise the score (prior 70 -> HIGH)
    p = pipe(StubScorer(0.40), StubCategory("DIGITAL_ARREST", 0.6))
    out = await p.analyse(BARE)
    assert out.risk_score == 40 and out.risk_level == "MEDIUM"  # text only; no category prior
    assert out.classification == "DIGITAL_ARREST"  # still reported, as a model guess
    c = out.extra["score_components"]
    assert c["text_score"] == 40 and c["category_prior"] is None
    assert c["category_used_in_score"] is False and c["url_adjustment"] == 0
    assert out.extra["score_components"]["formula"].startswith("risk = ")
    assert "category is a model guess and is NOT part of the score" in c["formula"]
    assert any("not used in the risk score" in x for x in out.limitations)
    with_url = await p.analyse({**BARE, "suspicious_url": "http://sbi-kyc-login.example.top/x"})
    assert with_url.risk_score > out.risk_score
    assert with_url.extra["score_components"]["url_adjustment"] >= 1
    assert with_url.risk_score <= 100


async def test_critical_level_and_static_guidance():
    out = await pipe(StubScorer(0.95)).analyse(BARE)
    assert out.risk_score == 95 and out.risk_level == "CRITICAL"
    assert any(a["source"] == "static_guidance" for a in out.recommended_actions)
    assert out.recommended_actions[0]["text"].startswith("Treat this as urgent")


async def test_abstention_gives_undetermined_with_confidence_and_no_prior():
    out = await pipe(StubScorer(0.1, ()), StubCategory("UPI_FRAUD", 0.10)).analyse(BARE)
    assert out.classification == "UNDETERMINED" and out.confidence == 0.1
    assert out.extra["score_components"]["category_prior"] is None
    # the stub scores 0.1 with no reason: unexplained points are ignored and recorded (2026-10-18)
    assert out.risk_score == 0 and out.risk_level == "LOW"
    assert out.extra["score_components"]["unexplained_scores_ignored"] == {"text_score": 10}
    assert out.extra["assessment"] == "no_indicators_found"
    assert out.capabilities["category_classifier"] == "available"


# ------------------------------------------------------------------ missing inputs
async def test_absent_fields_are_not_evidence_and_present_context_does_not_score():
    p = pipe(StubScorer(0.2, ()), StubCategory("OTHER", 0.5))
    bare = await p.analyse(BARE)
    full = await p.analyse({
        **BARE, "suspect_phone": "+917042918356", "amount": "5000", "city": "Pune",
        "state": "Maharashtra", "upi_id": "ravi@okaxis", "payment_method": "UPI",
        "latitude": 18.5, "longitude": 73.8,
    })  # fmt: skip
    # 0.2 from a stub with no reason is ignored; absent and present context change nothing
    assert bare.risk_score == full.risk_score == 0
    assert bare.risk_level == full.risk_level == "LOW"
    assert bare.extra["score_components"]["unexplained_scores_ignored"] == {"text_score": 20}
    assert bare.indicators == full.indicators
    assert bare.capabilities["url_heuristics"] == "skipped_no_input"
    assert bare.capabilities["entity_format_checks"] == "skipped_no_input"
    assert bare.capabilities["geographic_context"] == "skipped_no_input"
    assert full.capabilities["entity_format_checks"] == "available"
    assert full.capabilities["geographic_context"] == "available"
    assert full.extra["context"]["geographic"]["used_for_scoring"] is False
    assert "were not treated as evidence" in bare.explanation
    # text scorer saw only the text in both cases
    assert p._scorer.seen[0] == p._scorer.seen[1]


async def test_unusual_phone_or_upi_has_no_risk_weight():
    p = pipe(StubScorer(0.2, ()), StubCategory("OTHER", 0.5))
    a = await p.analyse(BARE)
    b = await p.analyse({**BARE, "suspect_phone": "+910000000000", "upi_id": "x1@weirdbank"})
    assert a.risk_score == b.risk_score
    notes = " ".join(n for c in b.extra["entity_checks"]["checks"] for n in c["notes"])
    assert "placeholder" in notes and "not evidence of fraud" in notes


async def test_url_stage_independent_of_other_stages_and_never_fetches():
    out = await pipe(StubScorer(boom=True), StubCategory(boom=True)).analyse(
        {**BARE, "suspicious_url": "http://192.168.1.9/login"}
    )
    assert out.capabilities["url_heuristics"] == "available"
    assert out.capabilities["url_fetch_reputation"] == "unavailable"
    assert out.extra["url_analysis"]["fetched"] is False
    assert {i["code"] for i in out.indicators} >= {"IP_LITERAL_HOST", "NOT_HTTPS"}


# ------------------------------------------------------------------ degradation
async def test_scorer_unavailable_is_partial_and_never_low():
    out = await broken_scorer_pipe(StubCategory("OTHER", 0.6)).analyse(BARE)
    assert out.status == "PARTIAL"
    assert out.capabilities["text_risk_scorer"] == "unavailable"
    assert out.risk_level is None and out.risk_score is None
    assert out.extra["score_components"]["withheld"] is True
    assert "text_risk_scorer" in out.extra["unavailable_capabilities"]
    assert any("not a finding that the report is low risk" in x for x in out.limitations)
    assert out.classification == "OTHER"  # other stages continued


async def test_scorer_unavailable_category_guess_alone_gives_no_level():
    # before 2026-10-18 the category prior turned this into HIGH 70 with no evidence at all
    out = await broken_scorer_pipe(StubCategory("DIGITAL_ARREST", 0.7)).analyse(BARE)
    assert out.status == "PARTIAL"
    assert out.risk_level is None and out.risk_score is None  # withheld, never LOW or HIGH
    assert out.classification == "DIGITAL_ARREST"
    assert out.extra["assessment"] == "not_assessed"


async def test_scorer_raising_at_runtime_is_flagged_not_benign():
    out = await pipe(StubScorer(boom=True), StubCategory("OTHER", 0.5)).analyse(BARE)
    assert out.capabilities["text_risk_scorer"] == "unavailable"
    assert out.risk_level is None and out.status == "PARTIAL"
    assert out.extra["capability_reasons"]["text_risk_scorer"] == "scoring_error"


async def test_nothing_could_be_scored_is_unavailable():
    out = await broken_scorer_pipe(None).analyse(BARE)  # category stub is replaced below
    p = ComplaintAnalysisPipeline(scorer_loader=lambda: (_ for _ in ()).throw(ImportError("x")),
                                  category_loader=lambda: None)  # fmt: skip
    out = await p.analyse(BARE)
    assert out.status == "UNAVAILABLE" and out.risk_score is None and out.risk_level is None
    assert out.classification is None and out.confidence is None
    assert out.capabilities["text_risk_scorer"] == "unavailable"
    assert out.capabilities["category_classifier"] == "unavailable"
    assert out.explanation  # the rule text still says what happened
    assert out.recommended_actions


async def test_classifier_unavailable_other_stages_continue():
    p = ComplaintAnalysisPipeline(scorer=StubScorer(0.6), category_loader=lambda: None)
    out = await p.analyse({**BARE, "suspicious_url": "https://bit.ly/abc"})
    assert out.classification is None and out.confidence is None
    assert out.capabilities["category_classifier"] == "unavailable"
    assert out.extra["capability_reasons"]["category_classifier"]
    assert out.capabilities["text_risk_scorer"] == "available"
    assert out.risk_score is not None and out.status == "PARTIAL"
    assert "URL_SHORTENER" in {i["code"] for i in out.indicators}


async def test_classifier_artifact_pin_failure_is_reported(monkeypatch, tmp_path):
    from complaints.ml import category_model as cm

    real = cm.load_with_status
    monkeypatch.setattr(cm, "load_with_status", lambda *a, **k: real(expected_sha256="0" * 64))
    out = await ComplaintAnalysisPipeline(scorer=StubScorer(0.3)).analyse(BARE)
    assert out.classification is None
    assert out.extra["capability_reasons"]["category_classifier"] == "pin_mismatch"


async def test_attachments_listed_as_unanalysed_and_make_partial():
    ev = [{"mime_type": "image/png", "size_bytes": 10}, {"mime_type": "audio/mpeg", "size_bytes": 5},
          {"mime_type": "image/png", "size_bytes": 11}]  # fmt: skip
    out = await pipe().analyse({**BARE, "evidence": ev})
    assert out.status == "PARTIAL"
    types = {(a["type"], a["mime_type"], a["count"]) for a in out.extra["unanalysed_attachments"]}
    assert types == {("screenshot_or_image", "image/png", 2), ("audio", "audio/mpeg", 1)}
    assert all(a["analysed"] is False for a in out.extra["unanalysed_attachments"])
    assert out.capabilities["audio_transcription"] == "unavailable"
    assert out.capabilities["screenshot_ocr"] == "unavailable"
    assert {"audio_transcription", "screenshot_ocr"} <= set(out.extra["unavailable_capabilities"])
    ok = await pipe().analyse(BARE)
    assert ok.status == "COMPLETED"  # no files, nothing applicable missing (LLM not configured)


# ------------------------------------------------------------------ Ollama (fake transport)
def ollama(handler, **kw) -> OllamaClient:
    return OllamaClient("http://localhost:11434", "qwen2.5:3b", 5.0,
                        transport=httpx.MockTransport(handler), **kw)  # fmt: skip


def ok_handler(captured: list):
    def h(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"response": "The text shows urgency. No link was opened."})

    return h


async def test_not_configured_rules_only():
    out = await pipe(explainer=OllamaClient(None)).analyse(BARE)
    assert out.extra["llm_explanation"] is None and out.extra["explanation_source"] == "rules"
    assert out.capabilities["llm_explanation"] == "unavailable"
    assert out.extra["capability_reasons"]["llm_explanation"] == "not_configured"
    assert out.status == "COMPLETED"  # an unconfigured optional step is not a failure


async def test_reachable_llm_adds_text_and_never_changes_score_or_level():
    cap: list = []
    base = await pipe(StubScorer(0.8), StubCategory("OTP_SCAM", 0.6)).analyse(BARE)
    out = await pipe(StubScorer(0.8), StubCategory("OTP_SCAM", 0.6),
                     explainer=ollama(ok_handler(cap))).analyse(BARE)  # fmt: skip
    assert out.extra["llm_explanation"] == "The text shows urgency. No link was opened."
    assert out.extra["llm_model"] == "qwen2.5:3b"
    assert out.capabilities["llm_explanation"] == "available" and out.status == "COMPLETED"
    assert (out.risk_score, out.risk_level, out.classification, out.confidence) == (
        base.risk_score, base.risk_level, base.classification, base.confidence,
    )  # fmt: skip
    assert out.explanation == base.explanation  # rules explanation is independent
    req = cap[0]
    assert req.url.path == "/api/generate"
    body = json.loads(req.content)
    assert body["model"] == "qwen2.5:3b" and body["stream"] is False
    assert body["options"]["temperature"] == 0.1 and body["options"]["num_predict"] == 120


async def test_prompt_contains_no_raw_phone_upi_email_url():
    cap: list = []
    desc = (
        "Caller +91 98765 43210 told me to pay ravi.kumar@okaxis or mail help@scam-bank.top and "
        "open https://sbi-kyc.example.top/login. Account 123456789012 was debited."
    )
    c = {**BARE, "description": desc, "suspect_phone": "+919876543210",
         "suspicious_url": "https://sbi-kyc.example.top/login", "upi_id": "ravi.kumar@okaxis",
         "suspect_email": "help@scam-bank.top"}  # fmt: skip
    await pipe(explainer=ollama(ok_handler(cap))).analyse(c)
    prompt = json.loads(cap[0].content)["prompt"]
    for raw in (
        "98765",
        "9876543210",
        "ravi.kumar",
        "okaxis",
        "help@",
        "scam-bank",
        "sbi-kyc",
        "example.top",
        "123456789012",
    ):
        assert raw not in prompt, raw
    assert "Account" not in prompt  # the complaint text itself is not sent (grounded prompt)
    s = sanitise("call 98765 43210 now")
    assert "98765" not in s


@pytest.mark.parametrize(
    ("handler", "cap_state"),
    [
        (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)), "timeout"),
        (lambda r: httpx.Response(503, text="busy"), "error"),
        (lambda r: httpx.Response(200, text="<html>not json</html>"), "error"),
        (lambda r: httpx.Response(200, json={"nope": 1}), "error"),
        (lambda r: httpx.Response(200, json={"response": 7}), "error"),
        (lambda r: httpx.Response(200, json={"response": "x" * (MAX_LLM_OUTPUT + 1)}), "rejected"),
        (
            lambda r: httpx.Response(200, json={"response": "Call 9876543210 to get it back."}),
            "rejected",
        ),
        (
            lambda r: httpx.Response(200, json={"response": "Visit https://evil.example/x now"}),
            "rejected",
        ),
        (lambda r: httpx.Response(200, json={"response": "mail me at a@b.example"}), "rejected"),
        (
            lambda r: httpx.Response(
                200, json={"response": "There is a 90% chance this is fraud."}
            ),
            "rejected",
        ),
        (lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused", request=r)), "unavailable"),
    ],
)
async def test_llm_failures_yield_null_flagged_and_unchanged_result(handler, cap_state):
    base = await pipe(StubScorer(0.8)).analyse(BARE)
    out = await pipe(StubScorer(0.8), explainer=ollama(handler)).analyse(BARE)
    assert out.extra["llm_explanation"] is None
    assert out.capabilities["llm_explanation"] == cap_state
    assert out.extra["capability_reasons"]["llm_explanation"]
    assert out.explanation == base.explanation and out.explanation  # rules text still returned
    assert (out.risk_score, out.risk_level, out.classification) == (
        base.risk_score, base.risk_level, base.classification,
    )  # fmt: skip
    # the LLM wording is optional: its failure is reported, not turned into a PARTIAL analysis
    assert out.status == base.status and "llm_explanation" in out.extra["unavailable_capabilities"]
    assert any("optional wording only" in x for x in out.limitations)


def test_validate_output_rules():
    prompt = "Complaint [number] [link]"
    assert validate_output("Short and safe.", prompt) is None
    assert validate_output("", prompt) == "bad_response"
    assert validate_output("see www.example.com", prompt) == "unsafe_content"
    assert validate_output("the likelihood is high", prompt) == "likelihood_claim"
    assert "probab" not in build_prompt("x" * 10, {"indicators": []}).split("Do not state a")[0]


async def test_ollama_probe_not_configured_reachable_unreachable_and_cache():
    assert await OllamaClient(None).probe() == "not_configured"
    calls: list = []
    now = [100.0]

    def ok(r):
        calls.append(r.url.path)
        return httpx.Response(200, json={"models": []})

    c = ollama(ok, clock=lambda: now[0])
    assert await c.probe() == "reachable" and await c.probe() == "reachable"
    assert calls == ["/api/tags"]  # cached
    now[0] += 31
    assert await c.probe() == "reachable" and len(calls) == 2
    down = ollama(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x", request=r)))
    assert await down.probe() == "unreachable"
    assert await ollama(lambda r: httpx.Response(500)).probe() == "unreachable"


# ------------------------------------------------------------------ API
@pytest.fixture
def v2(make_app):
    return make_app(analysis=pipe(StubScorer(0.8), StubCategory("OTP_SCAM", 0.6)))


@pytest.fixture
def v2client(v2):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=v2), base_url="http://t")


async def test_stored_analysis_has_old_fields_and_additive_v2_fields(v2client, tok):
    c = await create(v2client, tok("a"), category="OTP_SCAM", description=PHISH,
                     suspicious_url="http://sbi-kyc.example.top/login")  # fmt: skip
    r = await v2client.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))
    assert r.status_code == 201
    a = r.json()
    for k in (
        "id",
        "complaint_id",
        "version",
        "created_at",
        "pipeline_version",
        "status",
        "capabilities",
        "risk_score",
        "risk_level",
        "score_semantics",
        "classification",
        "confidence",
        "indicators",
        "explanation",
        "recommended_actions",
        "limitations",
    ):
        assert k in a, k
    for k in (
        "explanation_source",
        "llm_explanation",
        "capability_reasons",
        "url_analysis",
        "entity_checks",
        "unanalysed_attachments",
        "score_components",
        "category_model",
        "confidence_semantics",
        "context",
        "unavailable_capabilities",
    ):
        assert k in a, k
    assert a["explanation_source"] == "rules" and a["classification"] == "OTP_SCAM"
    assert a["score_semantics"] == "uncalibrated_heuristic"
    assert "probab" not in json.dumps(a).casefold()
    g = (await v2client.get(f"/complaints/{c['id']}/analysis", headers=tok("a"))).json()
    assert g["url_analysis"]["fetched"] is False and g["version"] == 1


async def test_critical_roundtrip(make_app, tok):
    app = make_app(analysis=pipe(StubScorer(0.97)))
    cl = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    c = await create(cl, tok("a"))
    a = (await cl.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))).json()
    assert a["risk_level"] == "CRITICAL" and a["risk_score"] == 97
    d = (await cl.get(f"/complaints/{c['id']}", headers=tok("a"))).json()
    assert d["latest_analysis"]["risk_level"] == "CRITICAL"
    with app.state.store.engine.connect() as conn:
        assert conn.execute(sql_text("select risk_level from analyses")).scalar_one() == "CRITICAL"
    alerts = (await cl.get("/alerts", headers=tok("a"))).json()["items"]
    assert [x["kind"] for x in alerts] == ["HIGH_RISK_ANALYSIS"]
    flt = (await cl.get("/complaints?risk_level=CRITICAL", headers=tok("a"))).json()
    assert flt["total"] == 1


async def test_evidence_files_are_listed_by_type_via_api(v2client, tok):
    c = await create(v2client, tok("a"))
    for name, data, ct in (("s.png", PNG, "image/png"), ("c.mp3", MP3, "audio/mpeg")):
        r = await v2client.post(f"/complaints/{c['id']}/evidence",
                                files={"file": (name, data, ct)}, headers=tok("a"))  # fmt: skip
        assert r.status_code == 201
    a = (await v2client.post(f"/complaints/{c['id']}/analysis", headers=tok("a"))).json()
    assert a["status"] == "PARTIAL"
    kinds = sorted(x["type"] for x in a["unanalysed_attachments"])
    assert kinds == ["audio", "screenshot_or_image"]
    assert "s.png" not in json.dumps(a) and "c.mp3" not in json.dumps(a)


async def test_legacy_row_without_detail_renders_with_defaults(v2client, tok, v2):
    c = await create(v2client, tok("a"))
    from complaints.schema import analyses

    with v2.state.store.engine.begin() as conn:
        import datetime as dt

        conn.execute(analyses.insert().values(
            id="legacy-1", complaint_id=c["id"], version=1,
            created_at=dt.datetime.now(dt.UTC), pipeline_version="complaints-text-v1+rules-v1",
            capabilities=dict(CAPABILITIES_NONE), risk_score=12, risk_level="LOW",
            classification=None, confidence=None, indicators=[], explanation=None,
            recommended_actions=[], limitations=["old"], status="COMPLETED",
        ))  # fmt: skip
    g = (await v2client.get(f"/complaints/{c['id']}/analysis", headers=tok("a"))).json()
    assert g["risk_level"] == "LOW" and g["explanation"] is None
    assert g["explanation_source"] is None and g["llm_explanation"] is None
    assert g["capability_reasons"] == {} and g["unanalysed_attachments"] == []


def test_existing_database_without_detail_column_is_upgraded(tmp_path):
    import sqlalchemy as sa

    from complaints.store import ComplaintStore

    url = f"sqlite:///{tmp_path / 'old.db'}"
    ComplaintStore(url).close()
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("ALTER TABLE analyses DROP COLUMN detail"))
    eng.dispose()
    st = ComplaintStore(url)  # create_schema adds the column back
    with st.engine.connect() as c:
        cols = {r["name"] for r in sa.inspect(c).get_columns("analyses")}
    st.close()
    assert "detail" in cols


# ------------------------------------------------------------------ POST /analyze
BODY = {"text": PHISH, "suspicious_url": "http://sbi-kyc.example.top/login",
        "suspect_phone": "+917042918356", "upi_id": "x1@okaxis", "amount": "4999"}  # fmt: skip


def _counts(app) -> dict[str, int]:
    out = {}
    with app.state.store.engine.connect() as c:
        for t in ("complaints", "analyses", "complaint_evidence", "alerts", "idempotency_keys"):
            out[t] = c.execute(sql_text(f"select count(*) from {t}")).scalar_one()
    return out


async def test_analyze_is_stateless_and_citizen_allowed(v2client, v2, tok):
    r0 = await v2client.get("/auth/session", headers=tok("a"))  # creates the user row
    assert r0.status_code == 200
    before = _counts(v2)
    r = await v2client.post("/analyze", json=BODY, headers=tok("a"))
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["persisted"] is False and "id" not in a and "complaint_id" not in a
    assert a["risk_score"] is not None and a["explanation_source"] == "rules"
    assert a["created_at"].endswith("+00:00") or a["created_at"].endswith("Z")
    assert a["capabilities"]["url_heuristics"] == "available"
    assert _counts(v2) == before
    assert "probab" not in json.dumps(a).casefold()
    assert (
        await v2client.post("/analyze", json={"text": PHISH}, headers=tok("a"))
    ).status_code == 200


async def test_analyze_requires_auth_and_validates_without_echo(v2client, tok):
    assert (await v2client.post("/analyze", json=BODY)).status_code == 401
    secret = "TOPSECRETVALUE9"
    r = await v2client.post("/analyze", json={"text": "qzv9"}, headers=tok("a"))
    assert r.status_code == 422 and "qzv9" not in r.text
    r = await v2client.post("/analyze", json={"text": PHISH, secret: 1}, headers=tok("a"))
    assert r.status_code == 422 and secret not in r.text
    r = await v2client.post("/analyze", json={"text": PHISH, "amount": "abc"}, headers=tok("a"))
    assert r.status_code == 422
    r = await v2client.post("/analyze", json={"text": "x" * 5001}, headers=tok("a"))
    assert r.status_code == 422
    r = await v2client.post("/analyze", json={"text": PHISH, "suspicious_url": "h" * 2049},
                            headers=tok("a"))  # fmt: skip
    assert r.status_code == 422


async def test_analyze_body_cap_and_rate_limit(make_app, settings, tok):
    import dataclasses

    s = dataclasses.replace(settings, rate_analyze_per_min=2, max_json_bytes=2048)
    app = make_app(settings=s, analysis=pipe())
    cl = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    big = await cl.post("/analyze", json={"text": "y" * 3000}, headers=tok("a"))
    assert big.status_code == 413
    for _ in range(2):
        assert (
            await cl.post("/analyze", json={"text": PHISH}, headers=tok("a"))
        ).status_code == 200
    r = await cl.post("/analyze", json={"text": PHISH}, headers=tok("a"))
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert (await cl.post("/analyze", json={"text": PHISH}, headers=tok("b"))).status_code == 200


async def test_analyze_logs_no_content(v2client, tok, caplog):
    caplog.set_level(logging.DEBUG)
    marker = "ZXQ-UNIQUE-MARKER-8841"
    body = {**BODY, "text": f"{PHISH} {marker}", "suspect_phone": "+917042918399"}
    r = await v2client.post("/analyze", json=body, headers=tok("a"))
    assert r.status_code == 200
    blob = "\n".join(f"{rec.getMessage()} {rec.__dict__.get('args', '')}" for rec in caplog.records)
    for needle in (marker, "7042918399", "sbi-kyc", "x1@okaxis", "KYC will expire"):
        assert needle not in blob


# ------------------------------------------------------------------ admin health
async def test_admin_health_default_not_configured(v2client, admin):
    h = (await v2client.get("/admin/health", headers=admin)).json()
    assert h["category_model"]["status"] == "loaded" and h["category_model"]["pin_ok"] is True
    assert h["category_model"]["version"] == "category-stub"
    assert h["ollama"]["status"] == "not_configured" and h["ollama"]["model"] is None
    assert h["call_guard"]["status"] in ("importable", "unavailable")


async def test_admin_health_ollama_reachable_and_unreachable(make_app, idp):
    from conftest import ADMIN_EMAIL

    hdr = bearer(idp.token(sub="user_admin", email=ADMIN_EMAIL))
    for handler, want in (
        (lambda r: httpx.Response(200, json={"models": []}), "reachable"),
        (lambda r: (_ for _ in ()).throw(httpx.ConnectError("no", request=r)), "unreachable"),
    ):
        app = make_app(analysis=pipe(), ollama=ollama(handler))
        cl = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        h = (await cl.get("/admin/health", headers=hdr)).json()
        assert h["ollama"] == {"status": want, "model": "qwen2.5:3b"}


async def test_admin_health_category_unavailable_when_pin_fails(make_app, idp):
    from conftest import ADMIN_EMAIL

    p = ComplaintAnalysisPipeline(scorer=StubScorer(), category_loader=lambda: None)
    app = make_app(analysis=p)
    cl = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    hdr = bearer(idp.token(sub="user_admin", email=ADMIN_EMAIL))
    h = (await cl.get("/admin/health", headers=hdr)).json()
    assert h["category_model"]["status"] == "unavailable" and h["category_model"]["pin_ok"] is False


def test_unavailable_model_import_is_lazy():
    import subprocess
    import sys

    code = (
        "import sys; import complaints.analysis, complaints.api; "
        "assert 'sklearn' not in sys.modules and 'joblib' not in sys.modules"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_no_paid_llm_client_imports():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "complaints"
    src = "\n".join(p.read_text() for p in root.rglob("*.py"))
    for banned in ("import anthropic", "import openai", "generativelanguage"):
        assert banned not in src


def test_outcome_dataclass_is_backward_compatible():
    o = AnalysisOutcome("COMPLETED", "t", {}, 1, "LOW", [], [], [])
    assert o.extra == {} and o.classification is None
