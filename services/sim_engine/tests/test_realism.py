import json
from datetime import timedelta
from decimal import Decimal

import numpy as np
from scipy import stats

from sim_engine.benign import gen_benign_txns
from sim_engine.scam import gen_scam_campaign
from sim_engine.world import IST, build_world


def test_benign_share_at_least_99_percent(benign, campaigns):
    scam = sum(len(c.txns) for c in campaigns)
    assert len(benign) > 5000
    assert len(benign) / (len(benign) + scam) >= 0.99


def test_no_upi_over_one_lakh(benign, campaigns):
    every = benign + [t for c in campaigns for t in c.txns]
    assert all(t.amount_inr <= Decimal("100000") for t in every if t.rail == "UPI")
    assert all(t.amount_inr <= Decimal("500000") for t in every if t.rail == "IMPS")


def test_amount_distribution_lognormal(benign):
    x = np.array([float(t.amount_inr) for t in benign if t.rail == "UPI"])
    shape, loc, scale = stats.lognorm.fit(x, floc=0)
    p = stats.kstest(x, "lognorm", args=(shape, 0, scale)).pvalue
    assert p > 0.01


def test_rail_mix(benign):
    n = len(benign)
    share = {r: sum(t.rail == r for t in benign) / n for r in ("UPI", "IMPS", "NEFT")}
    assert abs(share["UPI"] - 0.85) < 0.02
    assert abs(share["IMPS"] - 0.10) < 0.02
    assert abs(share["NEFT"] - 0.05) < 0.015


def test_diurnal_curve(benign):
    hours = np.bincount([t.ts.hour for t in benign if t.rail != "NEFT"], minlength=24)
    trough = hours[2:5].mean()
    evening = hours[18:22].mean()
    assert evening > 4 * trough
    assert hours.argmax() in range(17, 23)
    assert hours.argmin() in range(2, 6)


def test_neft_timestamps_on_half_hour_batches(benign):
    neft = [t for t in benign if t.rail == "NEFT"]
    assert neft
    assert all(t.ts.minute in (0, 30) and t.ts.second == 0 and t.ts.microsecond == 0 for t in neft)
    # 24x7: batches occur at night too
    assert any(t.ts.hour < 6 for t in neft)


def test_all_timestamps_ist_aware(benign, campaigns):
    stamps = [t.ts for t in benign] + [t.ts for c in campaigns for t in c.txns]
    stamps += [e.ts for c in campaigns for e in c.calls]
    assert all(s.tzinfo is not None and s.tzinfo.key == "Asia/Kolkata" for s in stamps)
    assert all(s.utcoffset() == timedelta(hours=5, minutes=30) for s in stamps)


def test_chronological_benign_stream(benign):
    ts = [t.ts for t in benign]
    assert ts == sorted(ts)


def test_citizens_have_state_district_and_banks(world):
    assert len(world.citizens) == 1500
    assert all(c.state and c.district for c in world.citizens)
    assert len({c.state for c in world.citizens}) >= 5
    assert len(world.banks) == 4


def test_no_raw_ids_in_events(world, benign, campaigns):
    raw = {c.citizen_id for c in world.citizens} | {a.account_id for a in world.accounts.values()}
    for c in campaigns:
        raw |= set(c.mule_account_ids) | {c.cashout_account_id}
    blob = json.dumps(
        [t.model_dump(mode="json") for t in benign[:2000]]
        + [t.model_dump(mode="json") for c in campaigns for t in c.txns]
        + [e.model_dump(mode="json") for c in campaigns for e in c.calls]
    )
    assert not any(r in blob for r in raw)
    assert all(t.payer_token.startswith("tok_") for t in benign[:200])


def test_scam_victim_transfer_follows_call_within_minutes(campaigns):
    for c in campaigns:
        for v in c.victim_tokens:
            calls = [e for e in c.calls if e.victim_token == v]
            vt = [t for t in c.txns if t.payer_token == v]
            assert len({e.call_id for e in calls}) == 1
            assert len(calls) >= 6  # multi-chunk call
            first_call, last_chunk = min(e.ts for e in calls), max(e.ts for e in calls)
            first_txn = min(t.ts for t in vt)
            assert first_txn > last_chunk
            assert first_txn - first_call < timedelta(minutes=30)
            assert first_txn - last_chunk < timedelta(minutes=10)


def test_scam_amounts_skew_high_split_and_repeat(campaigns, benign):
    med_benign = float(np.median([float(t.amount_inr) for t in benign]))
    for c in campaigns:
        for v in c.victim_tokens:
            vt = [t for t in c.txns if t.payer_token == v]
            assert len(vt) >= 2
            assert all(t.rail == "UPI" and t.amount_inr <= Decimal("100000") for t in vt)
            assert sum(t.amount_inr for t in vt) >= Decimal("20000")
            assert min(float(t.amount_inr) for t in vt) > 10 * med_benign


def test_mules_young_fan_in_fan_out(campaigns):
    for c in campaigns:
        mules = c.mule_chain[:-1]
        cashout = c.mule_chain[-1]
        assert 3 <= len(mules) <= 6
        inflow = [t for t in c.txns if t.payee_hash in mules]
        assert {t.payee_hash for t in inflow} == set(mules) or len(inflow) < len(mules)
        assert all(t.payee_account_age_days < 30 for t in c.txns)
        out = [t for t in c.txns if t.payee_hash == cashout]
        assert out and all(t.payer_token in c.mule_payer_tokens for t in out)
        assert len(c.cashout_points) >= 1
        assert all(6 < lat < 37 and 68 < lon < 98 for lat, lon in c.cashout_points)


def test_mule_chain_pass_through_under_one_hour(campaigns):
    for c in campaigns:
        mules = set(c.mule_chain[:-1])
        for t_in in (t for t in c.txns if t.payee_hash in mules):
            outs = [
                o
                for o in c.txns
                if o.payee_hash == c.mule_chain[-1]
                and o.ts >= t_in.ts
                and o.ts - t_in.ts <= timedelta(hours=1)
            ]
            assert outs, "inflow not forwarded within an hour"


def test_same_seed_is_deterministic():
    def snap(seed):
        w = build_world(seed, 300)
        b = [t.model_dump() for t in gen_benign_txns(w, 2, seed)]
        c = gen_scam_campaign(w, "c1", 4, seed)
        return b, [t.model_dump() for t in c.txns], [e.model_dump() for e in c.calls], c.mule_chain

    assert snap(5) == snap(5)
    assert snap(5) != snap(6)
    assert IST.key == "Asia/Kolkata"
