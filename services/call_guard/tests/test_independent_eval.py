"""CI gate on the frozen independent eval set (never used for training or rule tuning)."""

from datetime import UTC, datetime, timedelta

import pytest
from scam_contracts.models import CallEvent

from call_guard.model import Scorer, load_classifier
from call_guard.rules import CALL_RISK_THRESHOLD
from call_guard.session import InMemorySessionStore, SessionScorer
from tests.data.independent_eval import BENIGN_CALLS, SCAM_CALLS

T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


async def _call_max(scorer: SessionScorer, call_id: str, chunks: list[str], lang: str) -> float:
    best = 0.0
    for i, text in enumerate(chunks):
        ev = CallEvent(
            call_id=call_id,
            idempotency_key=f"{call_id}:{i}",
            victim_token="v",
            caller_number_hash="h",
            ts=T0 + timedelta(seconds=30 * i),
            transcript_chunk=text,
            channel="pstn",
            lang=lang,
        )
        best = max(best, (await scorer.update(ev)).score)
    return best


def test_set_shape():
    assert len(SCAM_CALLS) >= 40 and all(2 <= len(c) <= 4 for _, c in SCAM_CALLS)
    assert len(BENIGN_CALLS) >= 40 and sum(h for *_, h in BENIGN_CALLS) >= 20
    for lang in ("en", "hi-Latn", "hi"):
        assert sum(1 for lg, _ in SCAM_CALLS if lg == lang) >= 10


@pytest.mark.parametrize("use_clf", [True, False], ids=["blend", "rules_only"])
async def test_independent_call_level_metrics(use_clf):
    scorer = Scorer(load_classifier() if use_clf else None)
    hits = 0
    for i, (lang, chunks) in enumerate(SCAM_CALLS):
        ss = SessionScorer(InMemorySessionStore(), scorer)
        hits += await _call_max(ss, f"s{i}", chunks, lang) >= CALL_RISK_THRESHOLD
    fps = 0
    for i, (lang, chunks, _) in enumerate(BENIGN_CALLS):
        ss = SessionScorer(InMemorySessionStore(), scorer)
        fps += await _call_max(ss, f"b{i}", chunks, lang) >= CALL_RISK_THRESHOLD
    recall, fpr = hits / len(SCAM_CALLS), fps / len(BENIGN_CALLS)
    print(
        f"independent clf={use_clf}: scam recall {hits}/{len(SCAM_CALLS)}={recall:.3f} "
        f"benign FPR {fps}/{len(BENIGN_CALLS)}={fpr:.3f}"
    )
    if use_clf:
        assert recall >= 0.90
        assert fpr <= 0.02
    else:
        assert recall >= 0.70  # rules-only fallback is weaker by design
        assert fpr <= 0.02


@pytest.mark.parametrize("use_clf", [True, False], ids=["blend", "rules_only"])
def test_hard_negative_chunks_stay_below_half(use_clf):
    """Strict gate: every hard-negative chunk < 0.5, no exceptions."""
    scorer = Scorer(load_classifier() if use_clf else None)
    over = [
        c
        for _lang, chunks, hard in BENIGN_CALLS
        if hard
        for c in chunks
        if scorer.score_text(c)[0] >= 0.5
    ]
    assert over == []
