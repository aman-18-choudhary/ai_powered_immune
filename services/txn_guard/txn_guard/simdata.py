"""Labelled feature streams from the simulator, with a *realistically imperfect* call-risk signal.

Needs the optional ``train`` extra (sim-engine); the service never imports this module.

Per seed: build a world, generate benign traffic plus several scam campaigns (placed after a
warm-up so victims have payment history), replay everything in time order through an
``InMemoryHistoryStore`` and extract features *before* recording each transaction. Rows in the
warm-up window only build history.

Call risk (what call-guard would have told txn-guard) is attached with imperfection so that
"call risk == scam" is NOT learnable:

* each scam victim's call is detected with probability ``CALL_RECALL`` (0.85);
* detection lags the end of the call by 1-3 chunks (20-70 s each, as in the simulator), so the
  first transfer(s), which start 1-6 min after the last chunk, sometimes precede the risk;
* once raised the risk is re-emitted every minute for 20 minutes (score 0.70-1.00);
* ``SPURIOUS_RATE`` (1.5%) of benign transactions get a spurious risk of 0.70-0.95 raised
  1-10 minutes earlier (benign people on legitimate calls, e.g. bank customer care).
Mule-forward transactions get no call risk (the mule is not on a victim call).

Mule payers have no payment history by construction in the simulator, which would make "no
history" a leaked scam proxy. A random ``MULE_HISTORY_SHARE`` (40%) of mule payers therefore get
5-40 injected prior benign UPI transfers (lognormal amounts, 4 favourite payees, their own device)
before their first sweep; these rows only build history and are never emitted as examples.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import numpy as np
from scam_contracts.models import CallRisk, Transaction

from .features import extract_features
from .history import InMemoryHistoryStore

CALL_RECALL = 0.85
SPURIOUS_RATE = 0.015
DETECT_LAG_CHUNKS = (1, 3)  # inclusive
CHUNK_GAP_S = (20.0, 70.0)
RISK_REEMIT_EVERY = timedelta(minutes=1)
RISK_REEMIT_FOR = timedelta(minutes=20)
RISK_MODEL = "sim-call-guard"
MULE_HISTORY_SHARE = 0.4
HISTORY_PREFIX = "hist-"


@dataclass(frozen=True)
class LabelledTxn:
    txn: Transaction
    features: dict[str, float]
    label: int  # 1 = scam transaction (victim transfer or mule forward)
    role: str  # "benign" | "victim_transfer" | "mule_forward"


def _risk(token: str, call_id: str, score: float, ts) -> CallRisk:
    return CallRisk(
        call_id=call_id, victim_token=token, score=round(score, 3), reasons=[],
        model_version=RISK_MODEL, ts=ts,
    )  # fmt: skip


def _mule_history(rng: np.random.Generator, camp, world_start) -> list[tuple[object, int, object]]:
    out: list[tuple[object, int, object]] = []
    for tok in camp.mule_payer_tokens:
        if rng.random() >= MULE_HISTORY_SHARE:
            continue
        mine = [t for t in camp.txns if t.payer_token == tok]
        if not mine:
            continue
        first = min(t.ts for t in mine)
        n = int(rng.integers(5, 41))
        span = (first - timedelta(hours=1) - world_start).total_seconds()
        for i in range(n):
            ts = world_start + timedelta(seconds=float(rng.uniform(0, span)))
            amt = float(np.clip(rng.lognormal(6.0, 1.2), 10, 90_000))
            out.append(
                (
                    ts, 1,
                    Transaction(
                        txn_id=f"{HISTORY_PREFIX}{tok}-{i}",
                        idempotency_key=f"{HISTORY_PREFIX}{tok}-{i}",
                        bank_id=mine[0].bank_id, payer_token=tok,
                        payee_hash=f"{HISTORY_PREFIX}payee-{tok}-{int(rng.integers(4))}",
                        rail="UPI", amount_inr=Decimal(str(round(amt, 2))), ts=ts,
                        payee_account_age_days=900, device_id_token=mine[0].device_id_token,
                    ),
                )
            )  # fmt: skip
    return out


def build_stream(
    seed: int,
    n_citizens: int = 1500,
    days: int = 14,
    warmup_days: int = 5,
    n_campaigns: int = 6,
    victims_per_campaign: int = 8,
    call_recall: float = CALL_RECALL,
    spurious_rate: float = SPURIOUS_RATE,
) -> list[LabelledTxn]:
    from sim_engine.benign import gen_benign_txns
    from sim_engine.labels import GroundTruth
    from sim_engine.scam import gen_scam_campaign
    from sim_engine.world import build_world

    world = build_world(seed, n_citizens)
    benign = list(gen_benign_txns(world, days, seed))
    rng = np.random.default_rng([seed, 7007])
    campaigns = []
    for k in range(n_campaigns):
        start = world.start + timedelta(
            days=int(rng.integers(warmup_days, days - 1)), hours=float(rng.uniform(8, 18))
        )
        campaigns.append(
            gen_scam_campaign(world, f"tg{k}", victims_per_campaign, seed, start_ts=start)
        )
    truth = GroundTruth(campaigns)

    events: list[tuple[object, int, object]] = []  # (ts, order, payload) order: risk(0) < txn(1)
    for t in benign + [t for c in campaigns for t in c.txns]:
        events.append((t.ts, 1, t))
        if not truth.is_scam_txn(t.txn_id) and rng.random() < spurious_rate:
            ts = t.ts - timedelta(minutes=float(rng.uniform(1, 10)))
            events.append(
                (ts, 0, _risk(t.payer_token, f"spur-{t.txn_id}", rng.uniform(0.7, 0.95), ts))
            )
    for c in campaigns:
        events += _mule_history(rng, c, world.start)
    for c in campaigns:
        by_victim: dict[str, list] = {}
        for e in c.calls:
            by_victim.setdefault(e.victim_token, []).append(e)
        for tok, evs in by_victim.items():
            if rng.random() >= call_recall:
                continue
            lag = float(
                rng.uniform(
                    *CHUNK_GAP_S, int(rng.integers(DETECT_LAG_CHUNKS[0], DETECT_LAG_CHUNKS[1] + 1))
                ).sum()
            )
            first = max(e.ts for e in evs) + timedelta(seconds=lag)
            ts = first
            while ts <= first + RISK_REEMIT_FOR:
                events.append((ts, 0, _risk(tok, evs[-1].call_id, rng.uniform(0.7, 1.0), ts)))
                ts += RISK_REEMIT_EVERY
    events.sort(key=lambda e: (e[0], e[1]))  # type: ignore[arg-type, return-value]

    store = InMemoryHistoryStore()
    warm_end = world.start + timedelta(days=warmup_days)
    rows: list[LabelledTxn] = []
    for ts, _, payload in events:
        if isinstance(payload, CallRisk):
            store.record_call_risk(payload)
            continue
        txn: Transaction = payload  # type: ignore[assignment]
        if ts >= warm_end and not txn.txn_id.startswith(HISTORY_PREFIX):
            role = truth.txn_role(txn.txn_id) or "benign"
            feats = extract_features(txn, store.context_for(txn, txn.ts))
            rows.append(LabelledTxn(txn, feats, 0 if role == "benign" else 1, role))
        store.record_txn(txn)
    return rows


def to_matrix(rows: Sequence[LabelledTxn], names: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
    x = np.array([[r.features[n] for n in names] for r in rows], dtype=float)
    return x, np.array([r.label for r in rows], dtype=int)
