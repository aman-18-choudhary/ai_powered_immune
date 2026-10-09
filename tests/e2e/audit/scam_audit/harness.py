"""Run the hero scenario through the real services and return the evidence package.

Wiring (everything shares ONE in-memory bus; the ledger sees only ``Topics.LEDGER``):

* call-guard: the real ``SessionScorer`` + ``handle_event`` consumer path over every call event
  of the scenario (emits ``callrisk.alert`` entries and the CallRisk messages),
* txn-guard bank_a / bank_b: two ``TxnGuardService`` instances with the real hold stores and the
  ``BusAuditSink`` outbox; bank_b learns the antibody through the real antibody consumer,
* antibody-hub: the real ``AntibodyStore`` (SQLite) + ``Hub`` outbox drain,
* evidence-ledger: the real app (consumer, SQLite store, dev signing key), driven over HTTP for
  case creation and package export.
The package is then extracted and verified with the shipped ``verify.py`` in an isolated
interpreter, pinned to the ledger's public key.
"""

import asyncio
import hashlib
import io
import os
import subprocess
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from antibody_hub.hub import Hub
from antibody_hub.store import AntibodyStore
from call_guard.consumer import handle_event
from call_guard.model import Scorer as CallScorer
from call_guard.model import load_classifier
from call_guard.session import InMemorySessionStore, SessionScorer
from evidence_ledger.api import create_app as create_ledger_app
from evidence_ledger.keys import KeyRing, Signer
from evidence_ledger.verify import verify_chain
from scam_contracts.models import CallRisk, Transaction, TxnDecision
from scam_contracts.topics import Topics
from sim_engine.replay import build_hero_scenario
from sim_engine.world import World, build_world
from svckit.bus import InMemoryBus
from svckit.idempotency import InMemoryIdempotencyStore
from svckit.ledger import call_ref, emit_ledger, payee_ref, txn_ref
from txn_guard.antibody_cache import AntibodyLookup, InMemoryAntibodyCache
from txn_guard.consumer import run_antibody_consumer
from txn_guard.history import InMemoryHistoryStore
from txn_guard.holds import BusAuditSink, InMemoryHoldStore
from txn_guard.model import Scorer
from txn_guard.service import TxnGuardService

OTHER_CASE_SENTINEL = "OTHERCASE_SENTINEL_77"
CASE_ID = "hero-case"
OFFICER = {"X-Principal-Role": "officer", "X-Principal-Sub": "officer-1"}
CONFIRM_DELAY = timedelta(seconds=60)


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class LedgerClock:
    """Deterministic ledger receipt clock: one second per call from a fixed start."""

    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


@dataclass
class AuditRun:
    seed: int
    meta: Any
    world: World
    a_txns: list[Transaction]
    b_first: Transaction
    b_decision: TxnDecision
    a_call_id: str
    shared_hold_txn_id: str
    ledger_entries: list[dict[str, Any]]
    counts: Counter[str]
    quarantined: int
    chain_ok: bool
    case: dict[str, Any]
    package: bytes
    package_dir: Path
    verify_returncode: int
    verify_stdout: str
    verify_stderr: str
    public_key_hex: str
    bus_ledger_messages: int
    bus_ledger_distinct: int
    extras: dict[str, Any] = field(default_factory=dict)


def _warm(history: InMemoryHistoryStore, payer: str, device: str, first_ts: datetime) -> None:
    for i in range(40):
        history.record_txn(
            Transaction(
                txn_id=f"warm-{payer}-{i}", idempotency_key=f"warm-{payer}-{i}", bank_id="b",
                payer_token=payer, payee_hash=f"{i % 4:064x}", rail="UPI",
                amount_inr=Decimal(300 + (i * 37) % 400),
                ts=first_ts - timedelta(days=10) + timedelta(hours=i * 5),
                payee_account_age_days=900, device_id_token=device,
            )
        )  # fmt: skip


def _service(scorer: Scorer, bus: InMemoryBus, clock: Clock) -> TxnGuardService:
    cache = InMemoryAntibodyCache(clock=clock)
    return TxnGuardService(
        scorer, InMemoryHistoryStore(), InMemoryHoldStore(audit=BusAuditSink(bus), clock=clock),
        bus, InMemoryIdempotencyStore(), clock=clock, antibodies=AntibodyLookup(cache),
    )  # fmt: skip


async def _wait(cond: Any, what: str, timeout: float = 15.0) -> None:
    t0 = asyncio.get_running_loop().time()
    while not cond():
        if asyncio.get_running_loop().time() - t0 > timeout:
            raise TimeoutError(what)
        await asyncio.sleep(0.01)


async def run_hero_audit(
    seed: int, workdir: Path, *, world: World | None = None, scorer: Scorer | None = None
) -> AuditRun:
    world = world or build_world(5, 600)
    scorer = scorer or Scorer()
    sc = build_hero_scenario(world, seed=seed)
    meta = sc.metadata
    vt = sorted(
        (t for t in sc.txns if sc.campaign.txn_roles[t.txn_id] == "victim_transfer"),
        key=lambda t: t.ts,
    )
    a_txns = [t for t in vt if t.payer_token == meta.victim_a_token]
    b_first = next(t for t in vt if t.payer_token == meta.victim_b_token)
    bus = InMemoryBus()
    clock = Clock(min(e.ts for e in sc.calls))
    workdir.mkdir(parents=True, exist_ok=True)

    # --- evidence ledger (real app, consumer on the shared bus, dev key) -----------------------
    signer = Signer.from_seed(hashlib.sha256(b"scam-audit-dev-key").digest())
    keyring = KeyRing(signer)
    prev_env = {k: os.environ.get(k) for k in ("TRUST_GATEWAY_HEADERS", "LEDGER_GATEWAY_SECRET")}
    os.environ["TRUST_GATEWAY_HEADERS"] = "1"
    os.environ.pop("LEDGER_GATEWAY_SECRET", None)
    app = create_ledger_app(
        database_url=f"sqlite:///{workdir / 'ledger.db'}", bus=bus,
        clock=LedgerClock(datetime(2026, 1, 1, tzinfo=UTC)), keyring=keyring,
        checkpoint_every=50, maintenance_interval_s=0, consume=True,
    )  # fmt: skip
    hub_store = AntibodyStore(f"sqlite:///{workdir / 'hub.db'}", clock=clock)  # IST sim clock
    hub = Hub(hub_store, bus)
    ab_task = None
    try:
        async with app.router.lifespan_context(app):
            store = app.state.store
            # --- call-guard over every call event (real consumer path) --------------------------
            sessions = SessionScorer(InMemorySessionStore(), CallScorer(load_classifier()))
            cg_idem = InMemoryIdempotencyStore()
            for e in sorted(sc.calls, key=lambda e: e.ts):
                await handle_event(e, sessions, bus, cg_idem)
            risks = [CallRisk.model_validate_json(r) for _, r in bus.messages(Topics.CALL_RISK)]
            a_risks = [r for r in risks if r.victim_token == meta.victim_a_token]
            b_risks = [r for r in risks if r.victim_token == meta.victim_b_token]
            assert a_risks, "call-guard must alert on victim A's call"

            # --- bank A: call risks and victim transfers in time order ---------------------------
            clock.t = a_txns[0].ts
            bank_a, bank_b = _service(scorer, bus, clock), _service(scorer, bus, clock)
            _warm(bank_a.history, meta.victim_a_token, a_txns[0].device_id_token, a_txns[0].ts)
            _warm(bank_b.history, meta.victim_b_token, b_first.device_id_token, b_first.ts)
            ab_task = run_antibody_consumer(bus, bank_b, InMemoryIdempotencyStore(), "bank_b")
            events = sorted(
                [(r.ts, 0, r) for r in a_risks] + [(t.ts, 1, t) for t in a_txns],
                key=lambda x: (x[0], x[1]),
            )
            shared_hold: tuple[datetime, str] | None = None
            for ts, kind, obj in events:
                clock.t = ts
                if kind == 0:
                    await bank_a.handle_call_risk(obj)
                else:
                    d = await bank_a.handle_txn(obj)
                    if (
                        d.decision == "hold_verify"
                        and obj.payee_hash == meta.shared_mule_payee_hash
                        and shared_hold is None
                    ):
                        shared_hold = (obj.ts, obj.txn_id)
            assert shared_hold is not None, "A's transfer to the shared mule must be held"

            # --- analyst confirms the mule: hub antibody (store API + outbox drain) --------------
            clock.t = shared_hold[0] + CONFIRM_DELAY
            res = await hub.submit(
                "mule_account", meta.shared_mule_payee_hash, "bank_a", "analyst-1",
                "case-77", False, role="analyst",
            )  # fmt: skip
            assert res.created
            await _wait(
                lambda: bank_b.antibodies.lookup(meta.shared_mule_payee_hash) is not None,
                "antibody applied at bank_b",
            )
            # an unrelated case's entry lands inside the package span (must be redacted)
            await emit_ledger(
                bus, "txn-guard", "system:txn-guard", "hold.created",
                {"event_code": OTHER_CASE_SENTINEL}, case_refs=["case-other"],
            )  # fmt: skip
            await bank_a.holds.resolve(
                shared_hold[1], "confirm_block", "analyst-1", role="analyst"
            )  # fmt: skip

            # --- bank B: the victim's first transfer to the same mule ----------------------------
            clock.t = b_first.ts
            d_b = await bank_b.handle_txn(b_first)
            await bank_b.holds.drain_audit(b_first.txn_id)

            # --- later state changes: a hold upgrade on an unrelated payer (step_up -> hold_verify)
            # then the antibody is extended and finally revoked by the hub ----------------------
            extra_payer, extra_dev = "payer_extra_upgrade", "dev_extra"
            _warm(bank_a.history, extra_payer, extra_dev, b_first.ts)
            clock.t = b_first.ts + timedelta(minutes=5)
            x = Transaction(
                txn_id="txn_" + hashlib.sha256(b"extra").hexdigest()[:16], idempotency_key="x1",
                bank_id="bank_x", payer_token=extra_payer,
                payee_hash=hashlib.sha256(b"extra-payee").hexdigest(), rail="UPI",
                amount_inr=Decimal("6000"), ts=clock.t, payee_account_age_days=20,
                device_id_token=extra_dev,
            )  # fmt: skip
            dx = await bank_a.handle_txn(x)
            assert dx.decision == "step_up", dx.decision
            ups = await bank_a.handle_call_risk(
                CallRisk(call_id="extra-call", victim_token=extra_payer, score=0.95, reasons=[],
                         model_version="x", ts=clock.t + timedelta(minutes=1))
            )  # fmt: skip
            assert ups and ups[0].decision == "hold_verify"
            clock.t = clock.t + timedelta(hours=1)
            ext = await hub.submit(
                "mule_account", meta.shared_mule_payee_hash, "bank_a", "analyst-1", None, True,
                role="analyst",
            )  # fmt: skip
            assert ext.extended, (ext, clock.t)
            await hub.revoke(res.record["antibody_id"], "analyst-2", "confirmed false positive",
                             "bank_a", "analyst")  # fmt: skip

            # --- ledger catches up ---------------------------------------------------------------
            raws = [r for _, r in bus.messages(Topics.LEDGER)]
            want = len(set(raws))
            await _wait(lambda: store.head().seq >= want, "ledger consumed every entry")
            await asyncio.sleep(0.05)
            head = store.head().seq
            entries = [e.model_dump() for e in store.page(1, head + 1)]
            counts: Counter[str] = Counter(e["event_type"] for e in entries)
            quarantined = sum(v for k, v in counts.items() if k.startswith("ledger.entry_quar"))
            chain_ok = verify_chain(store.page(1, head + 1)).ok

            decisions = [
                TxnDecision.model_validate_json(r) for _, r in bus.messages(Topics.TXN_DECISIONS)
            ]

            # --- case + package over the real HTTP API -------------------------------------------
            a_call_id = a_risks[0].call_id
            case_refs = [txn_ref(a_txns[0].txn_id), payee_ref(meta.shared_mule_payee_hash),
                         call_ref(a_call_id)]  # fmt: skip
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                r = await c.post(
                    "/cases", headers=OFFICER,
                    json={"case_id": CASE_ID, "title": "Hero digital-arrest campaign",
                          "case_refs": case_refs},
                )  # fmt: skip
                assert r.status_code == 201, r.text
                case = r.json()
                p = await c.get(f"/packages/{CASE_ID}", headers=OFFICER)
                assert p.status_code == 200, p.text
                package = p.content
    finally:
        if ab_task is not None:
            ab_task.cancel()
            await asyncio.gather(ab_task, return_exceptions=True)
        hub_store.close()
        for k, v in prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    pkg_dir = workdir / "package"
    pkg_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(package)) as z:
        z.extractall(pkg_dir)
    proc = subprocess.run(
        [sys.executable, "-I", "-S", str(pkg_dir / "verify.py"), str(pkg_dir),
         "--trusted-pubkey", signer.public_raw.hex()],
        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, cwd=pkg_dir, timeout=120,
    )  # fmt: skip
    return AuditRun(
        seed=seed,
        meta=meta,
        world=world,
        a_txns=a_txns,
        b_first=b_first,
        b_decision=d_b,
        a_call_id=a_call_id,
        shared_hold_txn_id=shared_hold[1],
        ledger_entries=entries,
        counts=counts,
        quarantined=quarantined,
        chain_ok=chain_ok,
        case=case,
        package=package,
        package_dir=pkg_dir,
        verify_returncode=proc.returncode,
        verify_stdout=proc.stdout,
        verify_stderr=proc.stderr,
        public_key_hex=signer.public_raw.hex(),
        bus_ledger_messages=len(raws),
        bus_ledger_distinct=want,
        extras={
            "b_alerts": len(b_risks),
            "a_alerts": len(a_risks),
            "decisions": decisions,
            "call_risks": len(risks),
        },
    )


def raw_identifiers(run: AuditRun) -> dict[str, list[str]]:
    """Every raw identifier the simulator world holds for the hero's actors (must never appear)."""
    w, m = run.world, run.meta
    victims = [w.citizen_by_token(m.victim_a_token), w.citizen_by_token(m.victim_b_token)]
    ids: dict[str, list[str]] = {
        "citizen_id": [v.citizen_id for v in victims],
        "account_id": [v.account_id for v in victims],
        "device_id": [v.device_id for v in victims],
        "payer_token": [m.victim_a_token, m.victim_b_token],
    }
    acct = [a.account_id for a in w.accounts.values()]
    ids["any_world_account"] = acct
    ids["any_world_holder"] = [a.holder_id for a in w.accounts.values()]
    return ids


async def _main() -> None:  # pragma: no cover - manual demo
    import tempfile

    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    with tempfile.TemporaryDirectory() as d:
        run = await run_hero_audit(seed, Path(d))
        print(f"seed {seed}: verify exit {run.verify_returncode}")
        print(f"package {len(run.package)} bytes; entries {sum(run.counts.values())}")
        for k, v in sorted(run.counts.items()):
            print(f"  {k}: {v}")
        print(run.verify_stdout)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
