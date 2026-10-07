"""Hand-built scenarios for metric tests (no simulator needed)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scam_contracts.models import CallRisk, Transaction, TxnDecision
from sim_engine.labels import GroundTruth
from sim_engine.scam import Campaign

T0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


def txn(tid: str, rail: str = "UPI", amount: str = "1000", minutes: float = 0.0) -> Transaction:
    return Transaction(
        txn_id=tid, idempotency_key=f"k-{tid}", bank_id="b", payer_token=f"p-{tid}",
        payee_hash="h", rail=rail, amount_inr=Decimal(amount),  # type: ignore[arg-type]
        ts=T0 + timedelta(minutes=minutes), payee_account_age_days=5, device_id_token="d",
    )  # fmt: skip


def decision(t: Transaction, d: str) -> TxnDecision:
    score = {"allow": 0.1, "step_up": 0.6, "hold_verify": 0.9}[d]
    return TxnDecision(
        txn_id=t.txn_id,
        decision=d,
        score=score,
        reasons=[],
        model_version="t",
        ts=t.ts,  # type: ignore[arg-type]
    )


def call_risk(call_id: str, minutes: float, score: float = 0.9) -> CallRisk:
    return CallRisk(
        call_id=call_id, victim_token="v", score=score, reasons=[], model_version="t",
        ts=T0 + timedelta(minutes=minutes),
    )  # fmt: skip


def campaign(cid: str, n_victims: int, spacing_min: float = 10.0, rail: str = "UPI"):
    """n victim transfers spaced ``spacing_min`` apart (Rs 1000 * (i+1)), plus one mule forward."""
    c = Campaign(cid)
    for i in range(n_victims):
        t = txn(f"{cid}-v{i}", rail, str(1000 * (i + 1)), i * spacing_min)
        c.txns.append(t)
        c.txn_roles[t.txn_id] = "victim_transfer"
        c.victim_first_txn_ts.append(t.ts)
        c.victim_tokens.append(t.payer_token)
    m = txn(f"{cid}-m0", "UPI", "5000", n_victims * spacing_min + 5)
    c.txns.append(m)
    c.txn_roles[m.txn_id] = "mule_forward"
    return c


def truth_of(*camps: Campaign) -> GroundTruth:
    return GroundTruth(list(camps))
