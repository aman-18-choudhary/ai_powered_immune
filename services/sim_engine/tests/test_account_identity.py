"""Account identity assumption: ``payer_account_ref`` is the same keyed hash as ``payee_hash``,
so an account that is paid and later pays carries ONE hash."""

from sim_engine.scam import gen_scam_campaign


def test_mule_forward_payer_ref_equals_its_payee_hash(world):
    camp = gen_scam_campaign(world, "ident-1", 8, 31)
    inbound = {t.payee_hash for t in camp.txns if camp.txn_roles[t.txn_id] == "victim_transfer"}
    fwd = [t for t in camp.txns if camp.txn_roles[t.txn_id] == "mule_forward"]
    assert fwd and all(t.payer_account_ref for t in fwd)
    assert {t.payer_account_ref for t in fwd} == inbound  # every paid mule sweeps, same hash
    assert {t.payee_hash for t in fwd} == {camp.mule_chain[-1]}  # one cash-out sink


def test_victim_and_benign_payer_refs_match_payee_hash_of_same_account(world, benign):
    camp = gen_scam_campaign(world, "ident-2", 5, 32)
    by_token = {
        world.payer_token(c.citizen_id): world.payee_hash(c.account_id) for c in world.citizens
    }
    for t in [*camp.txns, *benign[:3000]]:
        if t.payer_token in by_token:
            assert t.payer_account_ref == by_token[t.payer_token]
    assert all(t.payer_account_ref for t in benign)
