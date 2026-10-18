"""Regression guards on the frozen SYNTHETIC dataset ``evaluation/datasets/accuracy_probes_v1.jsonl``.

Real pipeline (call-guard rules + clf-v1 + category-v1), no LLM. These are invariants and known
outcomes on 20 hand-written probes; they are not accuracy estimates. Known open failures are pinned
explicitly so that a fix shows up as a deliberate test change, never as silent drift.
"""

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("call_guard")
pytest.importorskip("sklearn")

from complaints.analysis import ComplaintAnalysisPipeline  # noqa: E402

DATASET = Path(__file__).resolve().parents[3] / "evaluation/datasets/accuracy_probes_v1.jsonl"
SHA256 = "c17a6209a3fbde447664ccb55d817bef3cefcb695d14e8c608be3162a8d7e863"
FLAGGED = {"MEDIUM", "HIGH", "CRITICAL"}
# open failure modes still present after the 2026-10-18 batch (to be removed by later fixes)
KNOWN_FALSE_POSITIVES = {"N1_bank_otp_sms"}  # fix 5: "do not share this OTP" misfires
KNOWN_FALSE_NEGATIVES = {  # fix 6: no rules yet for these scam types; shown as "no indicators"
    "S2_upi_collect", "S3_job_task", "S4_investment", "S5_kyc_phish", "S7_sextortion",
    "S8_victim_narrative", "S9_otp_theft_hi", "E3_selected_mismatch",
}  # fmt: skip


@pytest.fixture(scope="module")
def results():
    import asyncio

    raw = DATASET.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SHA256, "dataset v1 is frozen: add a new version"
    cases = [json.loads(x) for x in raw.decode().splitlines() if x.strip()]
    assert len(cases) == 20 and all(c["synthetic"] is True for c in cases)
    pipe = ComplaintAnalysisPipeline()

    async def run():
        out = {}
        for c in cases:
            r = await pipe.analyse({"category": c["selected_category"], "title": "",
                                    "description": c["text"], "evidence": []})  # fmt: skip
            out[c["id"]] = (c, r)
        return out

    return asyncio.run(run())


def test_no_flag_without_evidence(results):
    for cid, (_, r) in results.items():
        if r.risk_level in FLAGGED:
            ev = [i for i in r.indicators if i.get("source") in ("rule", "ml")]
            assert ev, f"{cid}: {r.risk_level} with no indicator"


def test_no_high_or_critical_without_a_rule_based_indicator(results):
    for cid, (_, r) in results.items():
        if r.risk_level in ("HIGH", "CRITICAL"):
            assert any(i.get("source") == "rule" for i in r.indicators), cid


def test_category_guess_never_contributes_to_the_score(results):
    for cid, (_, r) in results.items():
        comp = r.extra["score_components"]
        assert comp["category_prior"] is None and comp["category_used_in_score"] is False, cid


def test_every_scored_point_has_a_reason(results):
    for cid, (_, r) in results.items():
        if r.risk_score:
            assert r.indicators, f"{cid}: score {r.risk_score} with no indicator"


def test_false_positives_are_exactly_the_known_open_ones(results):
    fp = {cid for cid, (c, r) in results.items()
          if c["expected_label"] == "legit" and r.risk_level in FLAGGED}  # fmt: skip
    assert fp == KNOWN_FALSE_POSITIVES


def test_false_negatives_are_exactly_the_known_open_ones(results):
    fn = {cid for cid, (c, r) in results.items()
          if c["expected_label"] == "scam" and r.risk_level not in FLAGGED}  # fmt: skip
    assert fn == KNOWN_FALSE_NEGATIVES


def test_missed_scams_never_read_as_safe(results):
    for cid in KNOWN_FALSE_NEGATIVES:
        _, r = results[cid]
        a = r.extra["assessment"]
        assert a in ("no_indicators_found", "indicators_found"), (cid, a)
        if a == "no_indicators_found":
            assert any("not a finding that the message or event is safe" in x
                       for x in r.limitations), cid  # fmt: skip
        else:  # weak indicators only (for example URGENCY): listed, level LOW, never "safe"
            assert r.risk_level == "LOW" and r.indicators, cid
            # review 2026-10-18: weak-only results lacked the not-a-safety-finding limitation
            assert any("not a finding that the message or event is safe" in x
                       for x in r.limitations), cid  # fmt: skip
        assert "safe" not in (a or "")


def test_every_low_result_says_it_is_not_a_finding_of_safety(results):
    for cid, (_, r) in results.items():
        if r.risk_level == "LOW":
            assert any("not a finding that the message or event is safe" in x
                       for x in r.limitations), cid  # fmt: skip


def test_classic_digital_arrest_scripts_stay_high(results):
    for cid in ("S1_digital_arrest", "S6_hinglish_da"):
        _, r = results[cid]
        assert r.risk_level in ("HIGH", "CRITICAL") and r.extra["assessment"] == "indicators_found"


def test_insufficient_inputs_have_no_level(results):
    for cid in ("E1_empty", "E2_tiny"):
        _, r = results[cid]
        assert r.status == "INSUFFICIENT_INPUT" and r.risk_level is None
        assert r.extra["assessment"] == "insufficient_evidence"
