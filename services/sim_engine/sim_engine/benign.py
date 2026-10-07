"""Benign background payment traffic."""

from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np
from scam_contracts.models import IMPS_LIMIT_INR, UPI_LIMIT_INR, Transaction

from .world import Account, World, stable_id

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
N_FAVOURITES = 4
FAVOURITE_SHARE = 0.65  # most payments go to payees the user has paid before
FAV_WEIGHTS = np.array([0.4, 0.3, 0.2, 0.1])
RECURRING_SHARE = 0.25  # rent / EMI / salary-style monthly payment

ROUND_SPIKES = np.array(
    [10, 20, 50, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000, 2500, 3000, 5000,
     10000, 20000, 50000, 100000, 200000, 500000],
    dtype=float,
)  # fmt: skip


def _amounts(rng: np.random.Generator, rail: str, n: int) -> np.ndarray:
    mu, sigma, cap = AMOUNT_PARAMS[rail]
    x = rng.lognormal(mu, sigma, n)
    bad = (x > cap) | (x < 1.0)
    while bad.any():
        x[bad] = rng.lognormal(mu, sigma, int(bad.sum()))
        bad = (x > cap) | (x < 1.0)
    return _humanise(rng, x, cap)


def _humanise(rng: np.random.Generator, x: np.ndarray, cap: float) -> np.ndarray:
    """Real people type whole rupees and love round numbers: ~15% snap to a nearby round
    figure (100, 500, 1000...), ~65% whole rupees, ~10% keep paise."""
    out = np.round(x).astype(float)
    r = rng.random(len(x))
    paise = r < 0.10
    out[paise] = np.round(x[paise], 2)
    near = np.abs(np.log(x[:, None] / ROUND_SPIKES[None, :])).argmin(axis=1)
    close = np.abs(np.log(x / ROUND_SPIKES[near])) < 0.10
    snap = (r >= 0.10) & (r < 0.25) & close
    out[snap] = ROUND_SPIKES[near][snap]
    return np.minimum(np.maximum(out, 1.0), cap)


def _ceil_half_hour(ts: datetime) -> datetime:
    ts = ts.replace(second=0, microsecond=0)
    if ts.minute == 0 or ts.minute == 30:
        return ts
    return ts.replace(minute=30) if ts.minute < 30 else ts.replace(minute=0) + timedelta(hours=1)


def gen_benign_txns(world: World, days: int, seed: int) -> Iterator[Transaction]:
    rng = np.random.default_rng([seed, 2])
    cits = world.citizens
    n_c = len(cits)
    p_payer = np.array([c.activity for c in cits])
    p_payer = p_payer / p_payer.sum()
    mw = 1.0 / np.arange(1, len(world.merchants) + 1) ** 0.8
    mw = mw / mw.sum()

    def draw_payee(payer_idx: int) -> Account:
        if rng.random() < MERCHANT_SHARE:
            return world.merchants[int(rng.choice(len(world.merchants), p=mw))]
        j = int(rng.integers(n_c))
        if j == payer_idx:
            j = (j + 1) % n_c
        return world.accounts[cits[j].account_id]

    # affinity: each citizen has a few favourite payees (shops they frequent, family)
    favourites = [[draw_payee(i) for _ in range(N_FAVOURITES)] for i in range(n_c)]
    # recurring monthly payments (rent/EMI/salary-style): fixed payee, fixed amount, fixed day
    recurring: dict[int, tuple[int, float, str, Account]] = {}
    for i in np.flatnonzero(rng.random(n_c) < RECURRING_SHARE):
        amount = float(np.clip(round(rng.lognormal(np.log(12000), 0.6) / 500) * 500, 1500, 90000))
        recurring[int(i)] = (
            int(rng.integers(1, 29)), amount, str(rng.choice(["IMPS", "NEFT"])), draw_payee(int(i)),
        )  # fmt: skip

    lam = n_c * TXNS_PER_CITIZEN_PER_DAY
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
        payers = rng.choice(n_c, size=n, p=p_payer)
        # (ts, payer_idx, rail, amount, payee)
        items: list[tuple[datetime, int, str, float, Account]] = []
        for i in range(n):
            pi = int(payers[i])
            if rng.random() < FAVOURITE_SHARE:
                payee = favourites[pi][int(rng.choice(N_FAVOURITES, p=FAV_WEIGHTS))]
            else:
                payee = draw_payee(pi)
            items.append((rows[i], pi, str(rails[i]), float(amt[i]), payee))
        for pi, (due_day, amount, rail, payee) in recurring.items():
            if day0.day == due_day:
                ts = day0 + timedelta(hours=float(rng.uniform(8, 12)))
                items.append((ts, pi, rail, amount, payee))

        out: list[Transaction] = []
        for ts, pi, rail, amount, payee in items:
            payer = cits[pi]
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
                    amount_inr=Decimal(str(round(amount, 2))),
                    ts=ts,
                    payee_account_age_days=max(0, (ts - payee.opened_at).days),
                    device_id_token=world.device_token(payer.device_id),
                )
            )
        out.sort(key=lambda t: t.ts)
        yield from out
