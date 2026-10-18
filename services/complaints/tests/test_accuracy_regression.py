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
# open failure modes: batch 1 (2026-10-18) left N1 (false positive) and eight false negatives;
# accuracy batch 2 (rules-v2, same day) removed them. Re-adding a case here must be deliberate.
KNOWN_FALSE_POSITIVES: set[str] = set()
KNOWN_FALSE_NEGATIVES: set[str] = set()


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


def test_every_scam_case_has_a_rule_based_indicator(results):
    # batch 2: each of the eight previously missed scams is now backed by a deterministic rule
    for cid, (c, r) in results.items():
        if c["expected_label"] == "scam":
            assert any(i.get("source") == "rule" for i in r.indicators), cid
            assert r.extra["assessment"] == "indicators_found", cid


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
