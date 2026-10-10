"""Settings (environment variables, validated once) and the detection parameters."""

import os
from dataclasses import dataclass, field, fields

DAY_US = 86_400 * 1_000_000


@dataclass(frozen=True)
class Settings:
    retention_days: float = 30.0  # GRAPH_RETENTION_DAYS
    max_nodes: int = 200_000  # GRAPH_MAX_NODES
    passthrough_max_s: float = 3600.0  # GRAPH_PASSTHROUGH_MAX_S
    passthrough_ratio: float = 0.7  # GRAPH_PASSTHROUGH_RATIO: outflow >= ratio * inflow
    harm_horizon_days: float = 7.0  # GRAPH_HARM_HORIZON_DAYS
    max_response_nodes: int = 500  # GRAPH_MAX_RESPONSE_NODES
    max_pending_decisions: int = 50_000  # GRAPH_MAX_PENDING_DECISIONS
    pending_ttl_s: float = 86_400.0  # GRAPH_PENDING_TTL_S
    fanin_scan_cap: int = 5_000  # GRAPH_FANIN_SCAN_CAP: neighbours scanned per node
    min_size: int = 3  # GRAPH_MIN_RING_SIZE: accounts (mule + cash-out) excluding victims
    min_score: float = 0.35  # GRAPH_MIN_SCORE
    min_signalled_payers: int = 2  # GRAPH_MIN_SIGNALLED_PAYERS (an antibody can replace them)
    relay_min_payers: int = 2  # GRAPH_RELAY_MIN_PAYERS: distinct payers of a pass-through account
    relay_min_inr: int = 10_000  # GRAPH_RELAY_MIN_INR: smaller pass-through flows are not relays
    min_flagged_share: float = 0.10  # GRAPH_MIN_FLAGGED_SHARE: flagged / distinct payers of a mule
    min_mules: int = 2  # GRAPH_MIN_MULES: pass-through accounts per ring (antibody rings exempt)
    sink_min_share: float = 0.3  # GRAPH_SINK_MIN_SHARE: inflow share from ring relays of a cash-out
    device_max_accounts: int = 20  # GRAPH_DEVICE_MAX_ACCOUNTS: busier devices are ignored
    max_component: int = 2_000  # GRAPH_MAX_COMPONENT: expansion cap per detection run
    louvain_min_nodes: int = 12  # GRAPH_LOUVAIN_MIN_NODES
    max_rings: int = 1_000  # GRAPH_MAX_RINGS
    stable_after_s: float = 1_800.0  # GRAPH_STABLE_AFTER_S
    max_dirty: int = 100_000  # GRAPH_MAX_DIRTY
    detect_interval_s: float = 0.2  # GRAPH_DETECT_INTERVAL_S
    maintenance_interval_s: float = 60.0  # GRAPH_MAINTENANCE_INTERVAL_S
    max_ring_nodes: int = 2_000  # GRAPH_MAX_RING_NODES: flow-network size cap
    flow_victim_cap: int = 500  # GRAPH_FLOW_VICTIM_CAP: individually modelled victims per ring
    flow_timeout_s: float = 5.0  # GRAPH_FLOW_TIMEOUT_S
    heavy_concurrency: int = 2  # GRAPH_HEAVY_CONCURRENCY
    heavy_queue: int = 4  # GRAPH_HEAVY_QUEUE: heavy requests waiting for a slot (more: 429)
    takedown_pool: int = 40  # GRAPH_TAKEDOWN_POOL: greedy candidates on rings above this size
    future_skew_s: float = 3_600.0  # GRAPH_FUTURE_SKEW_S: events further ahead are rejected
    audit_retention_days: float = 7.0  # GRAPH_AUDIT_RETENTION_DAYS
    audit_sweep_batch: int = 200  # GRAPH_AUDIT_SWEEP_BATCH
    audit_sweep_interval_s: float = 5.0  # GRAPH_AUDIT_SWEEP_INTERVAL_S
    max_body_bytes: int = 16_384  # GRAPH_MAX_BODY_BYTES
    # derived, not read from the environment
    passthrough_max_us: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "passthrough_max_us", int(self.passthrough_max_s * 1_000_000))
        if self.retention_days <= 0 or self.max_nodes < 10 or self.min_size < 2:
            raise ValueError("invalid graph-twin settings")
        if not 0 < self.passthrough_ratio <= 1 or self.harm_horizon_days <= 0:
            raise ValueError("invalid graph-twin settings")

    @property
    def retention_us(self) -> int:
        return int(self.retention_days * DAY_US)

    @classmethod
    def from_env(cls) -> "Settings":
        kw: dict[str, object] = {}
        for f in fields(cls):
            if not f.init:
                continue
            raw = os.getenv("GRAPH_" + f.name.upper())
            if raw is None or raw == "":
                continue
            kw[f.name] = type(f.default)(raw)  # int / float
        return cls(**kw)  # type: ignore[arg-type]
