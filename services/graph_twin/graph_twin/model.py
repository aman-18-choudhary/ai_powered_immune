"""Ring and takedown value objects."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

MODEL_VERSION = "graph-v1"
VICTIM, MULE, CASH_OUT, UNKNOWN = "victim_payer", "mule", "cash_out", "unknown"
ACCOUNT_ROLES = (MULE, CASH_OUT, UNKNOWN)
STATUSES = ("new", "growing", "stable", "retired")


@dataclass
class Ring:
    ring_id: str
    members: dict[str, str]  # account node key -> role (mule | cash_out | unknown)
    core: frozenset[str]  # mule / cash-out (and antibody-flagged unknown) members
    first_detected_us: int = 0
    updated_us: int = 0
    version: int = 1
    status: str = "new"
    growth_us: int = 0  # last time membership / victims / inflow grew
    # statistics (recomputed from the graph)
    inflow_paise: int = 0
    victims: int = 0
    victims_signalled: int = 0
    victims_truncated: bool = False
    first_seen_us: int = 0
    last_seen_us: int = 0
    banks: tuple[str, ...] = ()
    antibody: bool = False
    shared_device: bool = False
    passthrough_share: float = 0.0
    score: float = 0.0
    confidence: float = 0.0
    merged_from: tuple[str, ...] = ()
    fail_since_us: int = 0  # first refresh pass of the current loss-of-evidence episode
    fail_passes: int = 0  # consecutive refresh passes without evidence (capped at the setting)
    edge_sig: str = ""  # digest of the aggregated in-edges of the members (drives the ETag)

    def roles_count(self) -> dict[str, int]:
        out = {MULE: 0, CASH_OUT: 0, UNKNOWN: 0}
        for r in self.members.values():
            out[r] = out.get(r, 0) + 1
        return out

    def snapshot(self) -> dict[str, Any]:
        """JSON-able, deterministic."""
        return {
            "ring_id": self.ring_id,
            "members": dict(sorted(self.members.items())),
            "core": sorted(self.core),
            "first_detected_us": self.first_detected_us,
            "growth_us": self.growth_us,
            "status": self.status,
            "inflow_paise": self.inflow_paise,
            "victims": self.victims,
            "victims_signalled": self.victims_signalled,
            "victims_truncated": self.victims_truncated,
            "first_seen_us": self.first_seen_us,
            "last_seen_us": self.last_seen_us,
            "banks": list(self.banks),
            "antibody": self.antibody,
            "shared_device": self.shared_device,
            "passthrough_share": round(self.passthrough_share, 4),
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "merged_from": list(self.merged_from),
            "edge_sig": self.edge_sig,
            "fail_since_us": self.fail_since_us,
            "fail_passes": self.fail_passes,
        }

    def stats_view(self) -> tuple[Any, ...]:
        """What counts as a change for the version counter (excludes timestamps of the run)."""
        s = self.snapshot()
        s.pop("growth_us")
        s.pop("fail_since_us")
        s.pop("fail_passes")
        return tuple(sorted((k, repr(v)) for k, v in s.items()))

    @classmethod
    def from_snapshot(cls, s: dict[str, Any], version: int, updated_us: int) -> "Ring":
        return cls(
            ring_id=s["ring_id"], members=dict(s["members"]), core=frozenset(s["core"]),
            first_detected_us=s["first_detected_us"], updated_us=updated_us, version=version,
            status=s["status"], growth_us=s["growth_us"], inflow_paise=s["inflow_paise"],
            victims=s["victims"], victims_signalled=s["victims_signalled"],
            victims_truncated=s["victims_truncated"], first_seen_us=s["first_seen_us"],
            last_seen_us=s["last_seen_us"], banks=tuple(s["banks"]), antibody=s["antibody"],
            shared_device=s["shared_device"], passthrough_share=s["passthrough_share"],
            score=s["score"], confidence=s["confidence"], merged_from=tuple(s["merged_from"]),
            edge_sig=s.get("edge_sig", ""), fail_since_us=s.get("fail_since_us", 0),
            fail_passes=s.get("fail_passes", 0),
        )  # fmt: skip


@dataclass(frozen=True)
class TakedownResult:
    harm_before_inr: Decimal
    harm_after_inr: Decimal
    reduction_pct: float
    removed: tuple[str, ...] = ()
    edges_cut: tuple[tuple[str, str], ...] = ()
    observed_after_ts_inr: Decimal | None = None  # hindsight, timeline only


@dataclass
class AuditRow:
    dedupe: str
    msg_key: str
    body: str


@dataclass
class RunResult:
    """What one detection run changed."""

    audit: list[AuditRow] = field(default_factory=list)
    touched_rings: set[str] = field(default_factory=set)
