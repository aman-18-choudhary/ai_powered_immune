"""Benign background payment traffic."""

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal

import numpy as np
from scam_contracts.models import IMPS_LIMIT_INR, UPI_LIMIT_INR, Transaction

from .world import World, stable_id

# Hour-of-day weights (IST): trough 2-5am, evening peak 19-21.
HOUR_WEIGHTS = np.array(
    [1.6, 0.9, 0.35, 0.25, 0.25, 0.5, 1.2, 2.4, 3.6, 4.4, 4.8, 5.0,
     5.4, 5.2, 4.8, 4.8, 5.2, 6.0, 7.2, 8.4, 8.6, 7.4, 5.0, 2.8]
)  # fmt: skip
HOUR_WEIGHTS = HOUR_WEIGHTS / HOUR_WEIGHTS.sum()

RAILS = np.array(["UPI", "IMPS", "NEFT"])
RAIL_P = np.array([0.85, 0.10, 0.05])
# lognormal (mu, sigma, cap) per rail
AMOUNT_PARAMS = {
    "UPI": (np.log(450.0), 1.4, float(UPI_LIMIT_INR)),
    "IMPS": (np.log(4000.0), 1.2, float(IMPS_LIMIT_INR)),
    "NEFT": (np.log(25000.0), 1.3, 5_000_000.0),
}
TXNS_PER_CITIZEN_PER_DAY = 1.5
MERCHANT_SHARE = 0.6


def _amounts(rng: np.random.Generator, rail: str, n: int) -> np.ndarray:
    mu, sigma, cap = AMOUNT_PARAMS[rail]
    x = rng.lognormal(mu, sigma, n)
    bad = (x > cap) | (x < 1.0)
    while bad.any():
        x[bad] = rng.lognormal(mu, sigma, int(bad.sum()))
        bad = (x > cap) | (x < 1.0)
    return np.round(x, 2)


def _ceil_half_hour(ts):
    ts = ts.replace(second=0, microsecond=0)
    if ts.minute == 0 or ts.minute == 30:
        return ts
    return ts.replace(minute=30) if ts.minute < 30 else ts.replace(minute=0) + timedelta(hours=1)


def gen_benign_txns(world: World, days: int, seed: int) -> Iterator[Transaction]:
    rng = np.random.default_rng([seed, 2])
    cits = world.citizens
    p_payer = np.array([c.activity for c in cits])
    p_payer = p_payer / p_payer.sum()
    mw = 1.0 / np.arange(1, len(world.merchants) + 1) ** 0.8
    mw = mw / mw.sum()
    lam = len(cits) * TXNS_PER_CITIZEN_PER_DAY
    counter = 0
    for d in range(days):
        day0 = world.start + timedelta(days=d)
        weekend = 1.15 if day0.weekday() >= 5 else 1.0
        per_hour = rng.poisson(lam * weekend * HOUR_WEIGHTS)
        rows = []
        for h, cnt in enumerate(per_hour):
            secs = rng.uniform(0, 3600, int(cnt))
            rows.extend(day0 + timedelta(hours=h, seconds=float(s)) for s in secs)
        n = len(rows)
        if n == 0:
            continue
        rails = rng.choice(RAILS, size=n, p=RAIL_P)
        amt = np.empty(n)
        for r in RAILS:
            m = rails == r
            amt[m] = _amounts(rng, str(r), int(m.sum()))
        payers = rng.choice(len(cits), size=n, p=p_payer)
        to_merchant = rng.random(n) < MERCHANT_SHARE
        merch = rng.choice(len(world.merchants), size=n, p=mw)
        peer = rng.integers(0, len(cits), size=n)
        out: list[Transaction] = []
        for i in range(n):
            payer = cits[int(payers[i])]
            if to_merchant[i]:
                payee = world.merchants[int(merch[i])]
            else:
                pc = cits[int(peer[i])]
                if pc.citizen_id == payer.citizen_id:
                    pc = cits[(int(peer[i]) + 1) % len(cits)]
                payee = world.accounts[pc.account_id]
            ts = rows[i]
            rail = str(rails[i])
            if rail == "NEFT":
                ts = _ceil_half_hour(ts)
            tid = stable_id("txn", "b", world.seed, seed, counter)
            counter += 1
            out.append(
                Transaction(
                    txn_id=tid,
                    idempotency_key=f"idem_{tid}",
                    bank_id=payer.bank_id,
                    payer_token=world.payer_token(payer.citizen_id),
                    payee_hash=world.payee_hash(payee.account_id),
                    rail=rail,  # type: ignore[arg-type]
                    amount_inr=Decimal(str(amt[i])),
                    ts=ts,
                    payee_account_age_days=max(0, (ts - payee.opened_at).days),
                    device_id_token=world.device_token(payer.device_id),
                )
            )
        out.sort(key=lambda t: t.ts)
        yield from out
