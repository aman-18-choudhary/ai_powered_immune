"""Smoke entrypoint: generate a small world and print a summary."""

from .benign import gen_benign_txns
from .scam import gen_scam_campaign
from .world import build_world

if __name__ == "__main__":
    w = build_world(1, 500)
    b = sum(1 for _ in gen_benign_txns(w, 1, 1))
    c = gen_scam_campaign(w, "demo", 10, 1)
    print(f"benign_txns={b} scam_txns={len(c.txns)} scam_calls={len(c.calls)}")
