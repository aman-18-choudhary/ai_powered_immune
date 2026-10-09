"""Redis-backed ``HistoryStore`` (sync client, same semantics as ``InMemoryHistoryStore``).

Per payer: running log-amount stats, a 24 h sorted set of ``(ts, amount)``, a device set, recent
call risks; per (payer, payee) pair: count, first-paid ts, largest amount, 24 h recent payments.
Timestamps are stored as integer microseconds since the epoch (exact in a double score).
``record_txn`` is one atomic Lua script (seen-marker + all counters), idempotent per ``txn_id``:
a failed call leaves nothing behind and a replay never double-counts. Missing / None / blank
stored fields are coerced to 0, never raised on.
Payer and payee identifiers are already tokens/hashes. The client is blocking: call it from async
code through ``asyncio.to_thread`` (``blocking = True`` tells the service to do that).
"""

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from scam_contracts.models import CallRisk, Transaction

from .history import CALL_RISK_WINDOW, VELOCITY_WINDOW, Context

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MICRO = timedelta(microseconds=1)
GUARD_TTL_S = 7 * 86400


def _us(ts: datetime) -> int:
    return (ts - EPOCH) // MICRO


def _dt(us: float | int) -> datetime:
    return EPOCH + int(us) * MICRO


def _num(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    if isinstance(v, bytes):
        v = v.decode()
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _s(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


_RECORD_LUA = """
if not redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[1]) then return 0 end
local x, us, amt = tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[5])
local n = tonumber(redis.call('HGET', KEYS[2], 'n')) or 0
local mean = tonumber(redis.call('HGET', KEYS[2], 'mean')) or 0
local m2 = tonumber(redis.call('HGET', KEYS[2], 'm2')) or 0
n = n + 1
local d = x - mean
mean = mean + d / n
m2 = m2 + d * (x - mean)
redis.call('HSET', KEYS[2], 'n', n, 'mean', string.format('%.17g', mean),
           'm2', string.format('%.17g', m2))
local cutoff = string.format('%.0f', us - tonumber(ARGV[6]))
redis.call('ZADD', KEYS[3], ARGV[3], ARGV[4])
redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', cutoff)
redis.call('ZADD', KEYS[5], ARGV[3], ARGV[4])
redis.call('ZREMRANGEBYSCORE', KEYS[5], '-inf', cutoff)
redis.call('SADD', KEYS[6], ARGV[7])
redis.call('HINCRBY', KEYS[4], 'n', 1)
local first = tonumber(redis.call('HGET', KEYS[4], 'first'))
if (not first) or us < first then redis.call('HSET', KEYS[4], 'first', ARGV[3]) end
local mx = tonumber(redis.call('HGET', KEYS[4], 'max')) or 0
if amt > mx then redis.call('HSET', KEYS[4], 'max', ARGV[5]) end
return 1
"""


class RedisHistoryStore:
    blocking = True

    def __init__(self, client: Any, prefix: str = "txnguard:h:") -> None:
        self._r = client
        self._p = prefix

    def _k(self, *parts: str) -> str:
        return self._p + ":".join(parts)

    def record_txn(self, txn: Transaction) -> None:
        """One atomic Lua script: the ``seen`` marker and every counter change commit together or
        not at all, so a failed call leaves nothing behind and the retry applies the full update."""
        payer, payee = txn.payer_token, txn.payee_hash
        amt, us = float(txn.amount_inr), _us(txn.ts)
        self._r.eval(
            _RECORD_LUA, 6,
            self._k("seen", txn.txn_id), self._k(payer, "stats"), self._k(payer, "recent"),
            self._k(payer, "pair", payee), self._k(payer, "pairrecent", payee),
            self._k(payer, "dev"),
            GUARD_TTL_S, repr(math.log(amt)), us, f"{txn.txn_id}|{amt!r}", repr(amt),
            _us(EPOCH + VELOCITY_WINDOW), txn.device_id_token,
        )  # fmt: skip

    def record_call_risk(self, risk: CallRisk) -> None:
        key = self._k(risk.victim_token, "risk")
        us = _us(risk.ts)
        pipe = self._r.pipeline(transaction=True)
        pipe.zadd(key, {f"{risk.call_id}|{risk.score!r}|{us}": us})
        pipe.zremrangebyscore(key, "-inf", us - _us(EPOCH + 2 * CALL_RISK_WINDOW))
        pipe.execute()

    def context_for(self, txn: Transaction, now: datetime | None = None) -> Context:
        payer, payee = txn.payer_token, txn.payee_hash
        stats = self._r.hgetall(self._k(payer, "stats")) or {}
        n = int(_num(stats.get(b"n", stats.get("n"))))
        mean = _num(stats.get(b"mean", stats.get("mean")))
        m2 = _num(stats.get(b"m2", stats.get("m2")))
        pair = self._r.hgetall(self._k(payer, "pair", payee)) or {}
        pn = int(_num(pair.get(b"n", pair.get("n"))))
        first_raw = pair.get(b"first", pair.get("first"))
        first = _dt(_num(first_raw)) if first_raw not in (None, b"", "") else None
        recent = tuple(
            (_dt(sc), _num(_s(m).split("|")[1]))
            for m, sc in self._r.zrange(self._k(payer, "recent"), 0, -1, withscores=True)
        )
        raw_risks = self._r.zrange(self._k(payer, "risk"), 0, -1, withscores=True)
        risks = tuple((_dt(sc), _num(_s(m).split("|")[1])) for m, sc in raw_risks)
        call_ids = tuple(_s(m).split("|")[0] for m, _ in raw_risks)
        return Context(
            now=now or datetime.now(UTC),
            payer_n=n,
            payer_log_mean=mean if n else 0.0,
            payer_log_std=math.sqrt(m2 / n) if n >= 2 else 0.0,
            recent=recent,
            payee_first_seen_ts=first,
            device_seen=bool(self._r.sismember(self._k(payer, "dev"), txn.device_id_token)),
            payee_n=pn,
            payee_max_amount=_num(pair.get(b"max", pair.get("max"))),
            payee_recent=tuple(
                (_dt(sc), _num(_s(m).split("|")[1]))
                for m, sc in self._r.zrange(
                    self._k(payer, "pairrecent", payee), 0, -1, withscores=True
                )
            ),
            call_risks=risks,
            call_ids=call_ids,
        )
