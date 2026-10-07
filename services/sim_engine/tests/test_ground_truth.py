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
    assert gt.has_mass_victimisation("camp-A") and not gt.has_mass_victimisation("camp-B")
    assert gt.mass_victimisation_ts_or_none("camp-A") == gt.mass_victimisation_ts("camp-A")
    assert gt.mass_victimisation_ts_or_none("camp-B") is None


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
    sizes = [len(v) for v in by_call.values()]
    assert min(sizes) <= 4 < 8 <= max(sizes)  # short first contacts and long calls
    assert all(len({e.lang for e in v}) == 1 for v in by_call.values())
    english = [
        " ".join(e.transcript_chunk for e in v).lower()
        for v in by_call.values()
        if v[0].lang == "en" and len(v) >= 5  # short first contacts omit the later stages
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


def test_no_placeholder_greeting_and_pretext_fits_authority(world):
    from sim_engine.calls import AUTH_PRETEXTS, PRETEXTS

    assert set(AUTH_PRETEXTS["trai"]) == {"sim"} and set(AUTH_PRETEXTS["customs"]) == {"parcel"}
    assert set(AUTH_PRETEXTS["cbi"]) <= {"laundering", "parcel"}
    assert set(AUTH_PRETEXTS["ed"]) == {"laundering"}
    assert all(p in PRETEXTS for ps in AUTH_PRETEXTS.values() for p in ps)
    texts = [e.transcript_chunk for c in [gen_scam_campaign(world, "g", 8, 5)] for e in c.calls]
    assert not any("Sir/Madam" in t or "{" in t for t in texts)
    ben = [e.transcript_chunk for e in gen_benign_calls(world, days=1, seed=2)]
    assert not any("{" in t for t in ben)


def test_benign_templates_not_memorisable():
    from sim_engine.calls import BENIGN, BENIGN_KINDS

    for kind in BENIGN_KINDS:
        assert len(BENIGN[kind]) == 3  # en, hi-Latn, hi
        for opens, bodies, closes in BENIGN[kind]:
            assert min(len(opens), len(bodies), len(closes)) >= 6


def test_benign_calls_include_video_and_long_calls(world):
    calls = list(gen_benign_calls(world, days=3, seed=4))
    by_call: dict[str, list] = {}
    for e in calls:
        by_call.setdefault(e.call_id, []).append(e)
    assert {"pstn", "voip", "video"} <= {e.channel for e in calls}
    sizes = [len(v) for v in by_call.values()]
    assert max(sizes) >= 9 and min(sizes) <= 4
