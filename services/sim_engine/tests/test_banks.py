"""SIM_BANKS: the compose stack runs two txn-guard instances, so the world must have exactly
those two banks (bank ids are positional: bank_sbi, bank_hdfc, ...)."""

import pytest

from sim_engine.api import banks_from_env
from sim_engine.replay import build_hero_scenario
from sim_engine.world import build_world


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_hero_with_two_banks_only_produces_the_two_configured_bank_ids(seed):
    w = build_world(seed, 2000, n_banks=2)
    sc = build_hero_scenario(w, seed=seed)
    assert {b.bank_id for b in w.banks} == {"bank_sbi", "bank_hdfc"}
    assert {t.bank_id for t in sc.txns} <= {"bank_sbi", "bank_hdfc"}
    m = sc.metadata
    assert {m.victim_a_bank, m.victim_b_bank} == {"bank_sbi", "bank_hdfc"}


def test_default_world_is_still_four_banks():
    assert len(build_world(1, 300).banks) == 4


def test_banks_from_env(monkeypatch):
    monkeypatch.delenv("SIM_BANKS", raising=False)
    assert banks_from_env() == 4
    monkeypatch.setenv("SIM_BANKS", "2")
    assert banks_from_env() == 2
    for bad in ("0", "9", "x", "-1", "2.5"):
        monkeypatch.setenv("SIM_BANKS", bad)
        with pytest.raises(ValueError):
            banks_from_env()
