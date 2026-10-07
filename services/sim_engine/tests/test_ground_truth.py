import pytest

from sim_engine.calls import (
    benign_call_corpus,
    gen_benign_calls,
    scam_call_corpus,
)
from sim_engine.labels import GroundTruth
from sim_engine.scam import gen_scam_campaign


def test_txn_labels(benign, campaigns):
    gt = GroundTruth(campaigns)
    for c in campaigns:
        assert all(gt.is_scam_txn(t.txn_id) for t in c.txns)
        assert all(gt.campaign_of(t.txn_id) == c.campaign_id for t in c.txns)
    assert not any(gt.is_scam_txn(t.txn_id) for t in benign)
    assert all(gt.campaign_of(t.txn_id) is None for t in benign[:100])
    roles = {gt.txn_role(t.txn_id) for t in campaigns[0].txns}
    assert roles == {"victim_transfer", "mule_forward"}


def test_txn_ids_unique(benign, campaigns):
    ids = [t.txn_id for t in benign] + [t.txn_id for c in campaigns for t in c.txns]
    assert len(ids) == len(set(ids))


def test_call_labels(world, campaigns):
    gt = GroundTruth(campaigns)
    benign_calls = list(gen_benign_calls(world, days=2, seed=3))
    assert benign_calls
    assert not any(gt.is_scam_call(e.call_id) for e in benign_calls)
    for c in campaigns:
        assert all(gt.is_scam_call(e.call_id) for e in c.calls)
        assert all(gt.campaign_of_call(e.call_id) == c.campaign_id for e in c.calls)


def test_signal_timestamps(campaigns):
    gt = GroundTruth(campaigns)
    a, b = campaigns
    assert gt.first_signal_ts("camp-A") == min(e.ts for e in a.calls)
    firsts = sorted(min(t.ts for t in a.txns if t.payer_token == v) for v in a.victim_tokens)
    assert gt.mass_victimisation_ts("camp-A") == firsts[9]
    assert gt.first_signal_ts("camp-A") < gt.mass_victimisation_ts("camp-A")
    with pytest.raises(ValueError):
        gt.mass_victimisation_ts("camp-B")  # only 6 victims
    with pytest.raises(KeyError):
        gt.first_signal_ts("nope")


def test_campaign_seed_reproducible_ground_truth(world):
    a = gen_scam_campaign(world, "x", 10, 99)
    b = gen_scam_campaign(world, "x", 10, 99)
    assert GroundTruth([a]).mass_victimisation_ts("x") == GroundTruth([b]).mass_victimisation_ts(
        "x"
    )


def test_scam_transcripts_realistic_and_bilingual(campaigns, world):
    calls = [e for c in campaigns for e in c.calls] + [
        e for c in [gen_scam_campaign(world, f"L{i}", 8, 40 + i) for i in range(3)] for e in c.calls
    ]
    langs = {e.lang for e in calls}
    assert "en" in langs and langs & {"hi", "hi-Latn"}
    by_call: dict[str, list] = {}
    for e in calls:
        by_call.setdefault(e.call_id, []).append(e)
    assert all(len(v) >= 6 for v in by_call.values())
    assert all(len({e.lang for e in v}) == 1 for v in by_call.values())
    english = [
        " ".join(e.transcript_chunk for e in v).lower()
        for v in by_call.values()
        if v[0].lang == "en"
    ]
    assert english
    for text in english:
        assert "digital arrest" in text or "video call" in text
        assert any(
            k in text
            for k in ("cbi", "enforcement directorate", "customs", "police", "trai", "narcotics")
        )
        assert "safe account" in text or "verification account" in text
        assert any(k in text for k in ("tell anyone", "anyone", "secret", "confidential"))
    assert any(any("ऀ" <= ch <= "ॿ" for ch in e.transcript_chunk) for e in calls) or any(
        e.lang == "hi-Latn" for e in calls
    )


def test_training_corpora_cover_classes():
    scam = scam_call_corpus(seed=1, n=60)
    ben = benign_call_corpus(seed=1, n=60)
    assert len(scam) == 60 and len(ben) == 60
    assert {k for _, k, _ in ben} >= {"bank_care", "delivery", "family", "telemarketing"}
    assert {lang for _, _, lang in scam} >= {"en", "hi-Latn"}
    assert scam_call_corpus(1, 60) == scam
    stext = " ".join(t for t, _, _ in scam).lower()
    assert "digital arrest" in stext and "safe account" in stext
    # benign calls must not contain the core scam markers
    assert "digital arrest" not in " ".join(t for t, _, _ in ben).lower()
