"""Regression guards on the frozen SYNTHETIC dataset v2 (``evaluation/datasets/accuracy_probes_v2.jsonl``).

Invariants, not accuracy estimates: the dataset was written by the same author as the rules (on
2026-10-18, before the rules), so passing it shows consistency, not real-world performance.
"""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("call_guard")
pytest.importorskip("sklearn")

from complaints.analysis import ComplaintAnalysisPipeline  # noqa: E402

DATASET = Path(__file__).resolve().parents[3] / "evaluation/datasets/accuracy_probes_v2.jsonl"
SHA256 = "d2966d6e357530c1aa7cc32db55d621092d1bef894d1018165c27d3b72ec5d15"
FLAGGED = {"MEDIUM", "HIGH", "CRITICAL"}


@pytest.fixture(scope="module")
def results():
    raw = DATASET.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SHA256, "dataset v2 is frozen: add a new version"
    cases = [json.loads(x) for x in raw.decode().splitlines() if x.strip()]
    assert len(cases) == 73 and all(c["synthetic"] is True for c in cases)
    pipe = ComplaintAnalysisPipeline()

    async def run():
        return {c["id"]: (c, await pipe.analyse({"category": c["selected_category"], "title": "",
                                                 "description": c["text"], "evidence": []}))
                for c in cases}  # fmt: skip

    return asyncio.run(run())


def test_no_medium_or_higher_without_a_rule_based_indicator(results):
    for cid, (_, r) in results.items():
        if r.risk_level in FLAGGED:
            assert any(i.get("source") == "rule" for i in r.indicators), cid


def test_no_legitimate_case_is_flagged(results):
    fp = {cid for cid, (c, r) in results.items()
          if c["expected_label"] == "legit" and r.risk_level in FLAGGED}  # fmt: skip
    assert fp == set()


def test_no_scam_case_is_missed(results):
    fn = {cid for cid, (c, r) in results.items()
          if c["expected_label"] == "scam" and r.risk_level not in FLAGGED}  # fmt: skip
    assert fn == set()


def test_ambiguous_cases_never_get_high_or_critical(results):
    for cid, (c, r) in results.items():
        if c["expected_label"] == "ambiguous":
            assert r.risk_level not in ("HIGH", "CRITICAL"), cid


def test_insufficient_inputs_have_no_level(results):
    for cid, (c, r) in results.items():
        if c["expected_label"] == "insufficient":
            assert r.risk_level is None and r.extra["assessment"] == "insufficient_evidence", cid


def test_genuine_links_raise_no_url_finding(results):
    for cid in ("L17_amazon_url", "L18_sbi_official_url", "L19_challan", "L25_video_kyc_link"):
        _, r = results[cid]
        assert not [i for i in r.indicators if i.get("stage") == "url_heuristics"], cid
        assert r.extra["links_in_text"] and all(x["verified"] is False and x["fetched"] is False
                                                 for x in r.extra["links_in_text"])  # fmt: skip


def test_every_low_result_says_it_is_not_a_finding_of_safety(results):
    for cid, (_, r) in results.items():
        if r.risk_level == "LOW":
            assert any("not a finding that the message or event is safe" in x
                       for x in r.limitations), cid  # fmt: skip
