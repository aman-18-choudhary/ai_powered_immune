import { useEffect, useState } from "react";
import { useRunStore } from "./runStore";

const nowMs = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

export interface SimClock {
  /** Simulated seconds (drives hold deadlines). */
  t: number;
  /** Position on the pacing timeline, seconds at 1x (drives the scrubber). */
  w: number;
}

/** Longest stretch we extrapolate between observations; polls and events re-anchor sooner. */
const MAX_EXTRAPOLATE_S = 1.2;

/**
 * Smooth clocks: the last observed (t, w) advanced at the run speed. `w` moves `speed` seconds per
 * second; `t` follows `w` at the locally observed rate (cinematic pacing compresses long gaps).
 */
export function useSimClock(intervalMs = 100): SimClock {
  const [v, setV] = useState<SimClock>(() => {
    const { run } = useRunStore.getState();
    return { t: run.t, w: run.wallOffset };
  });
  useEffect(() => {
    const read = () => {
      const { clock, run } = useRunStore.getState();
      const real = clock.running ? Math.min(MAX_EXTRAPOLATE_S, (nowMs() - clock.at) / 1000) : 0;
      const dw = real * clock.speed;
      const w = clock.w + dw;
      const t = clock.t + dw * clock.rate;
      setV({
        t: run.duration > 0 ? Math.min(run.duration, t) : t,
        w: run.wallDuration > 0 ? Math.min(run.wallDuration, w) : w,
      });
    };
    read();
    const h = setInterval(read, intervalMs);
    return () => clearInterval(h);
  }, [intervalMs]);
  return v;
}

/** Simulated seconds only (hold countdowns). */
export function useSimTime(intervalMs = 100): number {
  return useSimClock(intervalMs).t;
}
