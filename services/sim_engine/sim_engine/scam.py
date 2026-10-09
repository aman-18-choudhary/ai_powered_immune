"""Digital-arrest scam campaigns: calls -> victim transfers -> mule fan-in -> cash-out."""

import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np
from scam_contracts.models import IMPS_LIMIT_INR, UPI_LIMIT_INR, CallEvent, Transaction

from .calls import (
    LANG_P,
    LANGS,
    SCAM_VIDEO_CHANNEL_P,
    chunks_to_events,
    evasive_scam_chunks,
    pick_name,
    scam_call_chunks,
)
from .world import CASHOUT_CITIES, Account, World, stable_id

MASS_VICTIM_THRESHOLD = 10
PASS_THROUGH_MAX = timedelta(minutes=45)  # max delay from a mule inflow to its sweep
_SWEEP_WINDOW_MIN = 10  # inflows within this window are swept together
_SWEEP_DELAY_MAX_MIN = 20
assert timedelta(minutes=_SWEEP_WINDOW_MIN + _SWEEP_DELAY_MAX_MIN) <= PASS_THROUGH_MAX
UPI_DAILY_CAP = float(UPI_LIMIT_INR)  # one account cannot move more than this via UPI per day
IMPS_CHUNK_MAX = float(IMPS_LIMIT_INR) * 0.99


@dataclass
class Campaign:
    campaign_id: str
    calls: list[CallEvent] = field(default_factory=list)
    txns: list[Transaction] = field(default_factory=list)
    # event-safe: payee hashes of the mules in fan-in order, cash-out account hash last
    mule_chain: list[str] = field(default_factory=list)
    cashout_points: list[tuple[float, float]] = field(default_factory=list)
    victim_tokens: list[str] = field(default_factory=list)
    mule_payer_tokens: list[str] = field(default_factory=list)
    txn_roles: dict[str, str] = field(default_factory=dict)  # txn_id -> role
    victim_first_txn_ts: list[datetime] = field(default_factory=list)
    # raw ids: World-internal knowledge, never emitted in events
    mule_account_ids: list[str] = field(default_factory=list)
    cashout_account_id: str = ""


def _mk_account(world: World, rng: np.random.Generator, key: str, kind: str, when: datetime):
    acc_id = stable_id("acct", world.seed, key, n=12)
    age = int(rng.integers(2, 25))  # young: opened < 30 days before the campaign
    bank = world.banks[int(rng.integers(len(world.banks)))].bank_id
    acc = Account(
        acc_id, bank, stable_id("hold", world.seed, key), when - timedelta(days=age), kind
    )
    world.accounts[acc_id] = acc
    return acc


def _season(world: World, old: Account, when: datetime, seed: int, campaign_id: str) -> Account:
    """An aged mule account: 60-400 days old (deterministic, no RNG draw)."""
    age = 60 + zlib.crc32(f"{seed}:{campaign_id}:seasoned".encode()) % 341
    acc = Account(old.account_id, old.bank_id, old.holder_id, when - timedelta(days=age), old.kind)
    world.accounts[acc.account_id] = acc
    return acc


def _channel(rng: np.random.Generator) -> str:
    return str(rng.choice(["pstn", "voip", "video"], p=SCAM_VIDEO_CHANNEL_P))


def _irregular(rng: np.random.Generator, x: float, cap: float) -> float:
    """Scam amounts are not all round: some round to 1000s, most are irregular rupees,
    a few carry paise (e.g. fee-like or tax-like totals)."""
    r = rng.random()
    if r < 0.30:
        v = max(round(x / 1000.0) * 1000.0, 5000.0)
    elif r < 0.85:
        v = float(round(x))
    else:
        v = round(x, 2)
    return min(max(v, 5000.0), cap)


def _split(rng: np.random.Generator, total: float, cap: float, min_parts: int = 2) -> list[float]:
    """Split ``total`` into irregular parts, each <= cap, repeated transfers."""
    n = max(min_parts, int(np.ceil(total / (cap * 0.97))))
    w = rng.uniform(0.6, 1.4, n)
    return [_irregular(rng, total * wi / w.sum(), cap) for wi in w]


def _victim_plan(rng: np.random.Generator, total: float) -> list[tuple[str, float]]:
    """(rail, amount) transfers. UPI is capped at Rs 1,00,000 per account per day, so
    larger totals go out as IMPS (<= Rs 5,00,000 each) after the first UPI transfers."""
    if total <= UPI_DAILY_CAP:
        return [
            ("UPI", a) for a in _split(rng, total, UPI_DAILY_CAP * 0.99, 2 if total < 50_000 else 3)
        ]
    upi_part = float(rng.uniform(30_000, 0.99 * UPI_DAILY_CAP))
    plan = [("UPI", a) for a in _split(rng, upi_part, UPI_DAILY_CAP * 0.5, 2)]
    plan += [("IMPS", a) for a in _split(rng, total - upi_part, IMPS_CHUNK_MAX, 1)]
    return plan


def gen_scam_campaign(
    world: World,
    campaign_id: str,
    n_victims: int,
    seed: int,
    start_ts: datetime | None = None,
    victim_indices: list[int] | None = None,
    second_victim_gap: timedelta | None = None,
    shared_first_mule: bool = False,
    seasoned_shared_mule: bool = False,
    evasive_second_victim_call: bool = False,
) -> Campaign:
    """``victim_indices`` pins the victims (indices into world.citizens). With
    ``second_victim_gap``, the second victim's first transfer is moved to exactly that long
    after the first victim's last transfer (their calls shift with it). With
    ``shared_first_mule`` the second victim's FIRST transfer goes to a mule that the first victim
    already paid (no extra random draws, so every other value is unchanged); that shared mule is
    the payee of the first victim's FIRST (and largest) transfer. ``seasoned_shared_mule`` makes
    it an aged account (60-400 days, a purchased/rented account) so young-payee signals do not
    fire for the second victim. ``evasive_second_victim_call`` gives the second victim a real but
    off-template scam call (a relative-in-an-emergency deposit script) that the shipped
    call-guard does not alert on."""
    rng = np.random.default_rng([seed, zlib.crc32(campaign_id.encode()), 4])
    camp = Campaign(campaign_id)
    t0 = start_ts or (world.start + timedelta(days=1, hours=float(rng.uniform(9, 17))))

    # --- infrastructure: young mule accounts (3-6), shared devices, one cash-out account
    n_mules = int(rng.integers(3, 7))
    mules = [
        _mk_account(world, rng, f"{seed}:{campaign_id}:mule:{i}", "mule", t0)
        for i in range(n_mules)
    ]
    cashout = _mk_account(world, rng, f"{seed}:{campaign_id}:cashout", "cashout", t0)
    mule_devices = [
        world.device_token(stable_id("dev", world.seed, seed, campaign_id, k)) for k in range(2)
    ]
    camp.mule_account_ids = [m.account_id for m in mules]
    camp.cashout_account_id = cashout.account_id
    camp.mule_chain = [world.payee_hash(m.account_id) for m in mules] + [
        world.payee_hash(cashout.account_id)
    ]
    camp.mule_payer_tokens = [world.payer_token(m.holder_id) for m in mules]
    k = int(rng.integers(2, 5))
    for ci in rng.choice(len(CASHOUT_CITIES), size=k, replace=False):
        _, lat, lon = CASHOUT_CITIES[int(ci)]
        camp.cashout_points.append(
            (round(lat + float(rng.normal(0, 0.03)), 5), round(lon + float(rng.normal(0, 0.03)), 5))
        )
    numbers = [f"+91{int(rng.integers(6_000_000_000, 9_999_999_999))}" for _ in range(3)]
    caller_hashes = [world.phone_hash(x) for x in numbers]

    # --- victims, spaced so the campaign ramps over hours
    drawn = rng.choice(len(world.citizens), size=n_victims, replace=False)
    vic_idx = drawn if victim_indices is None else np.array(victim_indices)
    if len(vic_idx) != n_victims:
        raise ValueError("victim_indices must have n_victims entries")
    inflows: dict[str, list[tuple[datetime, float]]] = {m.account_id: [] for m in mules}
    t = t0
    txn_counter = 0
    camp_first_mules: list[int] = []

    def mk_txn(payer_token, payee_acc, rail, amount, ts, bank_id, device, role) -> Transaction:
        nonlocal txn_counter
        tid = stable_id("txn", "s", world.seed, seed, campaign_id, txn_counter)
        txn_counter += 1
        txn = Transaction(
            txn_id=tid,
            idempotency_key=f"idem_{tid}",
            bank_id=bank_id,
            payer_token=payer_token,
            payee_hash=world.payee_hash(payee_acc.account_id),
            rail=rail,
            amount_inr=Decimal(str(round(amount, 2))),
            ts=ts,
            payee_account_age_days=max(0, (ts - payee_acc.opened_at).days),
            device_id_token=device,
        )
        camp.txns.append(txn)
        camp.txn_roles[tid] = role
        return txn

    for vi, ci in enumerate(vic_idx):
        cit = world.citizens[int(ci)]
        vtoken = world.payer_token(cit.citizen_id)
        camp.victim_tokens.append(vtoken)
        t = t + timedelta(minutes=float(rng.exponential(35)))
        lang = str(rng.choice(LANGS, p=LANG_P))
        name = pick_name(rng, lang)
        caller = caller_hashes[int(rng.integers(len(caller_hashes)))]
        call_start = t
        events: list[CallEvent] = []
        if rng.random() < 0.35:  # short first-contact call, then the real one minutes later
            events = chunks_to_events(
                rng, f"s:{world.seed}:{seed}:{campaign_id}:{vi}:a", vtoken, caller, call_start,
                scam_call_chunks(rng, lang, name, "short"), lang, _channel(rng),
            )  # fmt: skip
            call_start = events[-1].ts + timedelta(minutes=float(rng.uniform(3, 10)))
        main = chunks_to_events(
            rng, f"s:{world.seed}:{seed}:{campaign_id}:{vi}:b", vtoken, caller, call_start,
            scam_call_chunks(rng, lang, name, "full"), lang, _channel(rng),
        )  # fmt: skip
        if evasive_second_victim_call and vi == 1:
            events = []
            main = chunks_to_events(
                rng, f"s:{world.seed}:{seed}:{campaign_id}:{vi}:e", vtoken, caller, t,
                evasive_scam_chunks(), "en", "pstn",
            )  # fmt: skip
        calls_of_victim = [*events, *main]
        # victim transfers follow the demand within minutes; skewed-high, split, repeated;
        # UPI is capped per day, so larger totals continue over IMPS
        total = float(np.clip(rng.lognormal(np.log(120_000), 0.7), 20_000, 600_000))
        ts = main[-1].ts + timedelta(minutes=float(rng.uniform(1, 6)))
        if vi == 1 and second_victim_gap is not None:
            last_a = max(x.ts for x in camp.txns if x.payer_token == camp.victim_tokens[0])
            shift = last_a + second_victim_gap - ts
            # B's call may now precede A's; only B's call->transfer order is preserved
            calls_of_victim = [e.model_copy(update={"ts": e.ts + shift}) for e in calls_of_victim]
            ts += shift
        camp.calls.extend(calls_of_victim)
        first = None
        vic_mules = rng.permutation(n_mules)[: max(2, min(n_mules, 3))]
        plan = _victim_plan(rng, total)
        if shared_first_mule and vi == 0:
            # the first victim's LARGEST transfer goes first (inside the 15-minute call-risk window,
            # so it is held on A's own strong signals) and its payee is the shared mule
            big = max(range(len(plan)), key=lambda i: plan[i][1])
            plan.insert(0, plan.pop(big))
            camp_first_mules.append(int(vic_mules[0]))
            if seasoned_shared_mule:
                mules[camp_first_mules[0]] = _season(
                    world, mules[camp_first_mules[0]], t0, seed, campaign_id
                )
        if shared_first_mule and vi == 1:
            shared = camp_first_mules[0]
            if int(vic_mules[0]) != shared:
                vic_mules = np.concatenate([[shared], vic_mules[vic_mules != shared]])
        device = world.device_token(cit.device_id)
        for pi, (rail, amt) in enumerate(plan):
            mule_i = int(vic_mules[pi % len(vic_mules)])
            mule = mules[mule_i]
            txn = mk_txn(vtoken, mule, rail, amt, ts, cit.bank_id, device, "victim_transfer")
            inflows[mule.account_id].append((ts, amt))
            first = first or txn
            ts = ts + timedelta(minutes=float(rng.uniform(1, 5)))
        camp.victim_first_txn_ts.append(first.ts)  # type: ignore[union-attr]

    # --- fan-out: each mule sweeps what it received to the cash-out account, <1h
    for mi, mule in enumerate(mules):
        rows = sorted(inflows[mule.account_id])
        i = 0
        while i < len(rows):
            window_end = rows[i][0] + timedelta(minutes=_SWEEP_WINDOW_MIN)
            batch = [r for r in rows[i:] if r[0] <= window_end]
            i += len(batch)
            amount = sum(a for _, a in batch)
            send_ts = batch[-1][0] + timedelta(minutes=float(rng.uniform(2, _SWEEP_DELAY_MAX_MIN)))
            rail = "UPI" if amount <= 50_000 else "IMPS"
            limit = UPI_DAILY_CAP * 0.99 if rail == "UPI" else IMPS_CHUNK_MAX
            while amount > 0.005:
                chunk = min(amount, limit)
                mk_txn(camp.mule_payer_tokens[mi], cashout, rail, chunk, send_ts, mule.bank_id,
                       mule_devices[mi % 2], "mule_forward")  # fmt: skip
                amount -= chunk
                send_ts += timedelta(minutes=float(rng.uniform(1, 4)))

    camp.calls.sort(key=lambda e: e.ts)
    camp.txns.sort(key=lambda x: x.ts)
    camp.victim_first_txn_ts.sort()
    return camp
