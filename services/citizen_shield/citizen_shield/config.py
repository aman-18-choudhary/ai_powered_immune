"""Settings (environment variables ``CITIZEN_*``, validated once)."""

import os
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Settings:
    call_guard_url: str = "http://call-guard:8000"  # CALL_GUARD_URL (read separately)
    call_guard_timeout_s: float = 1.5  # CITIZEN_CALL_GUARD_TIMEOUT_S
    breaker_failures: int = 5  # CITIZEN_BREAKER_FAILURES consecutive failures open the breaker
    breaker_open_s: float = 10.0  # CITIZEN_BREAKER_OPEN_S
    max_body_bytes: int = 65_536  # CITIZEN_MAX_BODY_BYTES
    assess_per_min: int = 60  # CITIZEN_ASSESS_PER_MIN per principal
    report_per_min: int = 10  # CITIZEN_REPORT_PER_MIN per principal
    peer_per_min: int = 1_200  # CITIZEN_PEER_PER_MIN per network peer (the gateway in the stack)
    limiter_max_keys: int = 50_000  # CITIZEN_LIMITER_MAX_KEYS
    report_retention_days: float = 90.0  # CITIZEN_REPORT_RETENTION_DAYS
    prune_interval_s: float = 600.0  # CITIZEN_PRUNE_INTERVAL_S

    def __post_init__(self) -> None:
        if self.call_guard_timeout_s <= 0 or self.breaker_failures < 1 or self.breaker_open_s < 0:
            raise ValueError("invalid citizen-shield settings")
        if min(self.assess_per_min, self.report_per_min, self.peer_per_min) < 1:
            raise ValueError("invalid citizen-shield settings")
        if self.report_retention_days <= 0 or self.max_body_bytes < 1024:
            raise ValueError("invalid citizen-shield settings")

    @classmethod
    def from_env(cls) -> "Settings":
        kw: dict[str, object] = {}
        for f in fields(cls):
            if f.name == "call_guard_url":
                raw = os.getenv("CALL_GUARD_URL")
            else:
                raw = os.getenv("CITIZEN_" + f.name.upper())
            if raw is None or raw == "":
                continue
            kw[f.name] = type(f.default)(raw)
        return cls(**kw)  # type: ignore[arg-type]
