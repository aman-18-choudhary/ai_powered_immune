"""Settings (environment variables ``GEO_*``, validated once)."""

import os
from dataclasses import dataclass, fields

DAY_S = 86_400.0


@dataclass(frozen=True)
class Settings:
    retention_days: float = 30.0  # GEO_RETENTION_DAYS: older complaints are dropped and pruned
    window_s: float = 3600.0  # GEO_WINDOW_S: default hotspot window
    min_window_s: float = 60.0  # GEO_MIN_WINDOW_S
    max_window_s: float = 7 * DAY_S  # GEO_MAX_WINDOW_S
    baseline_windows: int = 6  # GEO_BASELINE_WINDOWS: trailing windows used as the baseline
    z_threshold: float = 3.0  # GEO_Z_THRESHOLD
    min_count: int = 3  # GEO_MIN_COUNT: a hotspot needs at least this many in the window
    min_rate: float = 0.5  # GEO_MIN_RATE: floor of the baseline rate per window (Poisson mean)
    # GEO_FUTURE_SKEW_S: events further ahead of the clock are dropped (never move `as_of`)
    future_skew_s: float = 300.0
    default_limit: int = 100  # GEO_DEFAULT_LIMIT
    max_limit: int = 500  # GEO_MAX_LIMIT
    max_body_bytes: int = 16_384  # GEO_MAX_BODY_BYTES
    prune_interval_s: float = 300.0  # GEO_PRUNE_INTERVAL_S
    prune_batch: int = 5_000  # GEO_PRUNE_BATCH
    max_retries: int = 3  # GEO_MAX_RETRIES (consumer; then DLQ)
    retry_backoff_s: float = 0.5  # GEO_RETRY_BACKOFF_S

    def __post_init__(self) -> None:
        if self.retention_days <= 0 or self.baseline_windows < 1 or self.min_rate <= 0:
            raise ValueError("invalid geo-intel settings")
        if not 0 < self.min_window_s <= self.window_s <= self.max_window_s:
            raise ValueError("invalid geo-intel settings")
        if not 1 <= self.default_limit <= self.max_limit:
            raise ValueError("invalid geo-intel settings")
        # the baseline (B windows) plus the current window must fit the retention: clamp the cap
        fit = self.retention_s / (self.baseline_windows + 1)
        if self.max_window_s > fit:
            object.__setattr__(self, "max_window_s", fit)
        if self.window_s > self.max_window_s:
            raise ValueError("GEO_WINDOW_S does not fit GEO_RETENTION_DAYS with the baseline")

    @property
    def retention_s(self) -> float:
        return self.retention_days * DAY_S

    @classmethod
    def from_env(cls) -> "Settings":
        kw: dict[str, object] = {}
        for f in fields(cls):
            raw = os.getenv("GEO_" + f.name.upper())
            if raw is None or raw == "":
                continue
            kw[f.name] = type(f.default)(raw)
        return cls(**kw)  # type: ignore[arg-type]
