"""Cross-bank antibody cache: the bank-side copy of the hub's active mule set.

Feed: ``Antibody`` events from ``antibody.published`` (seconds-level propagation) plus a startup
bootstrap from the hub's exact snapshot (``GET /antibodies/exact``).

Merge rules (the hub contract; events are at-least-once and may be reordered):

* ``revoked`` is sticky per ``antibody_id``: a tombstone removes the entry and a later
  non-revoked event for the SAME id (older or even newer expiry) never resurrects it. A NEW
  ``antibody_id`` (next generation) for the same key re-activates.
* otherwise the greatest ``expires_at`` wins; an event whose ``expires_at <= now`` is ignored and
  ``contains`` never returns an expired entry (``sweep`` purges them).
* ``apply`` is idempotent, so the (antibody_id, revoked, expires_at) dedupe key needs no state.

Staleness / failure behaviour: a new antibody reaches a bank in the bus latency (milliseconds to
seconds); a bank that was offline catches up only via bootstrap, so there is a window in which a
freshly confirmed mule is NOT enforced (fail-open for unknown antibodies). If the hub is
unreachable at boot, ``bootstrap`` logs a WARNING, bumps ``bootstrap_failed`` (exported as a
metric) and the service runs with the events-only cache; it never crashes.

Capacity: the in-memory cache holds ``capacity`` entries (default 100,000, ~200 bytes each, about
20 MB); beyond that the soonest-expiring entries are evicted (those lapse first anyway). The Redis
cache bounds memory by per-key TTL = remaining lifetime instead of a count.
No raw identifiers are stored or logged: keys are the federation-keyed hashes.
"""

import base64
import hashlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from scam_contracts.models import Antibody

log = logging.getLogger("txn_guard")

DEFAULT_CAPACITY = 100_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MICRO = timedelta(microseconds=1)


@dataclass(frozen=True)
class CachedAntibody:
    antibody_id: str
    kind: str
    key_hash: str
    expires_at: datetime


class AntibodyCache(Protocol):
    def apply(self, event: Antibody) -> None: ...

    def contains(self, key_hash: str, kind: str = "mule_account") -> CachedAntibody | None: ...

    def sweep(self) -> int: ...

    def size(self) -> int: ...

    def delta_keys(self) -> set[str]: ...

    def reset_delta(self) -> None: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InMemoryAntibodyCache:
    def __init__(
        self, clock: Callable[[], datetime] = _utcnow, capacity: int = DEFAULT_CAPACITY
    ) -> None:
        self._clock = clock
        self.capacity = capacity
        self._entries: dict[tuple[str, str], tuple[str, datetime]] = {}
        self._tombs: dict[str, datetime] = {}
        self._delta: set[str] = set()
        self._lock = threading.Lock()

    def apply(self, event: Antibody) -> None:
        now = self._clock()
        if event.expires_at <= now:
            return  # lapsed on arrival: nothing to enforce, nothing to remember
        k = (event.kind, event.key_hash)
        with self._lock:
            self._delta.add(event.key_hash)
            if event.revoked:
                self._tombs[event.antibody_id] = max(
                    event.expires_at, self._tombs.get(event.antibody_id, event.expires_at)
                )
                cur = self._entries.get(k)
                if cur is not None and cur[0] == event.antibody_id:
                    del self._entries[k]
                return
            if event.antibody_id in self._tombs:
                return  # sticky revoke
            cur = self._entries.get(k)
            if cur is not None and cur[1] > now and cur[1] >= event.expires_at:
                return  # the existing entry (same or another generation) lasts at least as long
            self._entries[k] = (event.antibody_id, event.expires_at)
            if len(self._entries) > self.capacity:
                self._evict(now)

    def _evict(self, now: datetime) -> None:
        for kk in [kk for kk, (_, exp) in self._entries.items() if exp <= now]:
            del self._entries[kk]
        while len(self._entries) > self.capacity:
            victim = min(self._entries, key=lambda kk: self._entries[kk][1])
            del self._entries[victim]
            log.warning("antibody cache over capacity; evicted the soonest-expiring entry")

    def contains(self, key_hash: str, kind: str = "mule_account") -> CachedAntibody | None:
        cur = self._entries.get((kind, key_hash))
        if cur is None or cur[1] <= self._clock():
            return None
        return CachedAntibody(cur[0], kind, key_hash, cur[1])

    def sweep(self) -> int:
        now = self._clock()
        with self._lock:
            dead = [kk for kk, (_, exp) in self._entries.items() if exp <= now]
            for kk in dead:
                del self._entries[kk]
            for aid in [a for a, exp in self._tombs.items() if exp <= now]:
                del self._tombs[aid]
            return len(dead)

    def size(self) -> int:
        return len(self._entries)

    def delta_keys(self) -> set[str]:
        return self._delta

    def reset_delta(self) -> None:
        self._delta = set()


_APPLY_LUA = """
local exp, now = tonumber(ARGV[3]), tonumber(ARGV[4])
if exp <= now then return 0 end
local ttl = math.floor((exp - now) / 1000)
if ARGV[2] == '1' then
  local old = tonumber(redis.call('PTTL', KEYS[2])) or -2
  if old < ttl then redis.call('SET', KEYS[2], '1', 'PX', ttl) end
  if redis.call('HGET', KEYS[1], 'id') == ARGV[1] then redis.call('DEL', KEYS[1]) end
  return 1
end
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
local cid = redis.call('HGET', KEYS[1], 'id')
local cexp = tonumber(redis.call('HGET', KEYS[1], 'exp') or '0')
if cid and cexp > now and cexp >= exp then return 0 end
redis.call('HSET', KEYS[1], 'id', ARGV[1], 'exp', ARGV[3])
redis.call('PEXPIRE', KEYS[1], ttl)
return 1
"""


def _us(t: datetime) -> int:
    return (t - EPOCH) // MICRO


class RedisAntibodyCache:
    """Same semantics over a (sync) Redis client; shared by processes of one bank. Entries live
    for their remaining lifetime (TTL) and a tombstone for the antibody's original lifetime."""

    def __init__(
        self, client: Any, clock: Callable[[], datetime] = _utcnow,
        capacity: int = DEFAULT_CAPACITY, prefix: str = "txnguard:ab:",
    ) -> None:  # fmt: skip
        self._r, self._clock, self.capacity, self._p = client, clock, capacity, prefix
        self._delta: set[str] = set()

    def apply(self, event: Antibody) -> None:
        self._delta.add(event.key_hash)
        self._r.eval(
            _APPLY_LUA, 2, f"{self._p}e:{event.kind}:{event.key_hash}",
            f"{self._p}t:{event.antibody_id}", event.antibody_id, "1" if event.revoked else "0",
            _us(event.expires_at), _us(self._clock()),
        )  # fmt: skip

    def contains(self, key_hash: str, kind: str = "mule_account") -> CachedAntibody | None:
        h = self._r.hgetall(f"{self._p}e:{kind}:{key_hash}")
        if not h:
            return None
        g = lambda f: h.get(f.encode(), h.get(f))  # noqa: E731
        try:
            exp = EPOCH + int(g("exp")) * MICRO
            aid = g("id").decode() if isinstance(g("id"), bytes) else str(g("id"))
        except (TypeError, ValueError):
            return None
        return None if exp <= self._clock() else CachedAntibody(aid, kind, key_hash, exp)

    def sweep(self) -> int:
        return 0  # Redis TTLs purge expired entries and tombstones

    def size(self) -> int:
        return sum(1 for _ in self._r.scan_iter(f"{self._p}e:*"))

    def delta_keys(self) -> set[str]:
        return self._delta

    def reset_delta(self) -> None:
        self._delta = set()


# ------------------------------------------------------------------------ bloom (optional)
class BloomView:
    """Read side of the hub's Bloom snapshot (same double-hashing scheme as the hub)."""

    def __init__(self, snap: dict[str, Any]) -> None:
        self.m, self.k = int(snap["m"]), int(snap["k"])
        self._bits = base64.b64decode(snap["bits"])
        if len(self._bits) * 8 != self.m:
            raise ValueError("bit array does not match m")
        self.version = str(snap.get("version", ""))

    def contains(self, item: str) -> bool:
        d = hashlib.sha256(item.encode()).digest()
        h1, h2 = int.from_bytes(d[:8], "big"), int.from_bytes(d[8:16], "big") | 1
        for i in range(self.k):
            p = (h1 + i * h2) % self.m
            if not self._bits[p >> 3] & (1 << (p & 7)):
                return False
        return True


class AntibodyLookup:
    """What the service uses: exact-cache lookup with optional Bloom negative pre-check.

    A Bloom hit never blocks by itself (it is confirmed against the exact cache and counted in
    ``bloom_unconfirmed`` when the cache disagrees). A Bloom miss is trusted only for keys not
    touched by an event since the snapshot (the snapshot is older than the event stream)."""

    def __init__(self, cache: AntibodyCache, use_bloom: bool = False) -> None:
        self.cache = cache
        self.use_bloom = use_bloom
        self._bloom: BloomView | None = None
        self.stats: dict[str, float] = {
            "hits": 0, "bloom_unconfirmed": 0, "bootstrap_ok": 0, "bootstrap_failed": 0,
            "bootstrap_entries": 0,
        }  # fmt: skip

    def load_bloom(self, snap: dict[str, Any]) -> None:
        self._bloom = BloomView(snap)
        self.cache.reset_delta()

    def lookup(self, key_hash: str, kind: str = "mule_account") -> CachedAntibody | None:
        bloom = self._bloom if self.use_bloom else None
        if bloom is not None and not bloom.contains(key_hash):
            if key_hash not in self.cache.delta_keys():
                return None  # definite miss in the snapshot and nothing newer arrived
        hit = self.cache.contains(key_hash, kind)
        if hit is None:
            if bloom is not None and bloom.contains(key_hash):
                self.stats["bloom_unconfirmed"] += 1
            return None
        self.stats["hits"] += 1
        return hit


# ------------------------------------------------------------------------------ hub client
class HubClient(Protocol):
    async def exact_page(
        self, bank_id: str, since: str | None = None, limit: int = 500
    ) -> dict[str, Any]: ...

    async def bloom(self, bank_id: str, etag: str | None = None) -> dict[str, Any] | None: ...


class HttpxHubClient:
    """Calls the hub through the gateway trust gate: ``X-Principal-Role: bank``,
    ``X-Principal-Bank`` and (when configured) ``X-Gateway-Secret``."""

    def __init__(
        self, base_url: str, bank_id: str, gateway_secret: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None, timeout: float = 5.0,
    ) -> None:  # fmt: skip
        self._client = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=timeout)
        self._headers = {"X-Principal-Role": "bank", "X-Principal-Sub": f"txn-guard-{bank_id}",
                         "X-Principal-Bank": bank_id}  # fmt: skip
        if gateway_secret:
            self._headers["X-Gateway-Secret"] = gateway_secret

    async def exact_page(
        self, bank_id: str, since: str | None = None, limit: int = 500
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"bank_id": bank_id, "limit": limit}
        if since:
            params["since"] = since
        r = await self._client.get("/antibodies/exact", params=params, headers=self._headers)
        r.raise_for_status()
        return r.json()

    async def bloom(self, bank_id: str, etag: str | None = None) -> dict[str, Any] | None:
        headers = dict(self._headers)
        if etag:
            headers["If-None-Match"] = etag
        r = await self._client.get(
            "/antibodies/bloom", params={"bank_id": bank_id}, headers=headers
        )
        if r.status_code == 304:
            return None
        r.raise_for_status()
        return r.json()

    async def aclose(self) -> None:
        await self._client.aclose()


async def bootstrap(
    cache: AntibodyCache, client: HubClient, bank_id: str,
    clock: Callable[[], datetime] = _utcnow, stats: dict[str, float] | None = None,
    limit: int = 500, max_pages: int = 10_000,
) -> int:  # fmt: skip
    """Load the hub's exact active set into ``cache`` (merge rules apply, so a tombstone seen on
    the event stream is not undone). Never raises: on failure logs a WARNING, counts it and
    returns the number applied so far."""
    stats = stats if stats is not None else {}
    n, cursor = 0, None
    try:
        for _ in range(max_pages):
            page = await client.exact_page(bank_id, cursor, limit)
            for it in page.get("items", []):
                now = clock()
                cache.apply(
                    Antibody(
                        antibody_id=it["antibody_id"],
                        kind=it["kind"],
                        key_hash=it["key_hash"],
                        source_bank="hub",
                        confirmed_by="hub-snapshot",
                        created_at=now,
                        expires_at=datetime.fromisoformat(it["expires_at"]),
                        revoked=False,
                    )
                )
                n += 1
            cursor = page.get("next_cursor")
            if not cursor:
                break
    except Exception:
        stats["bootstrap_failed"] = stats.get("bootstrap_failed", 0) + 1
        log.warning("antibody bootstrap failed; running with the events-only cache", exc_info=True)
        return n
    stats["bootstrap_ok"] = stats.get("bootstrap_ok", 0) + 1
    stats["bootstrap_entries"] = n
    return n
