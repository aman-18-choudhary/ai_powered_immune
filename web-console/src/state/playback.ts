import { endpoints } from "@/lib/api/endpoints";
import { ApiError, friendlyError } from "@/lib/api/client";
import { toast } from "./toasts";
import { useRunStore } from "./runStore";

/** UI speed 1x = 4 simulated seconds per wall second (server range 0.25..20). */
export const SPEED_BASE = 4;
export const SPEED_OPTIONS = [0.5, 1, 2, 5] as const;
/** Cinematic pacing plays the hero in about 75 s at 1x; the fallback (real pacing) is much slower. */
export const DEFAULT_UI_SPEED = 1;
const FALLBACK_UI_SPEED = 5;

/**
 * With `pacing: "cinematic"` (server compresses long simulated gaps) 1x is the server's 1x;
 * with real pacing 1x = SPEED_BASE simulated seconds per second. Set after a successful start.
 */
let cinematic = false;
export const isCinematic = () => cinematic;
const base = () => (cinematic ? 1 : SPEED_BASE);

export function uiToServerSpeed(m: number): number {
  return Math.min(20, Math.max(0.25, +(m * base()).toFixed(2)));
}
export function serverToUiSpeed(s: number): number {
  return +(s / base()).toFixed(2);
}

async function guarded<T>(label: string, fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch (e) {
    toast.error(label, friendlyError(e));
    return null;
  }
}

export const playback = {
  async start(uiSpeed?: number, seed = 1) {
    // Cinematic pacing is requested by default. Defensive path only: a server that rejects the
    // field (422) is retried with real pacing at a faster default.
    let r: { run_id: string } | null = null;
    try {
      cinematic = true;
      r = await endpoints.demoStart({ seed, speed: uiToServerSpeed(uiSpeed ?? DEFAULT_UI_SPEED), mode: "hero", pacing: "cinematic" });
    } catch (e) {
      cinematic = false;
      if (e instanceof ApiError && e.code === "invalid_request") {
        r = await guarded("Could not start the scenario", () =>
          endpoints.demoStart({ seed, speed: uiToServerSpeed(uiSpeed ?? FALLBACK_UI_SPEED), mode: "hero" }),
        );
      } else {
        toast.error("Could not start the scenario", friendlyError(e));
      }
    }
    if (r) {
      const st = await guarded("Could not read run state", () => endpoints.demoState());
      if (st) useRunStore.getState().mergeState(st);
    }
    return r;
  },
  async control(action: "pause" | "resume" | "stop" | "seek", extra: { t?: number; wall?: number; speed?: number } = {}) {
    const st = await guarded("Playback control failed", () => endpoints.demoControl({ action, ...extra }));
    if (st) useRunStore.getState().mergeState(st);
    return st;
  },
  pause: () => playback.control("pause"),
  resume: () => playback.control("resume"),
  stop: () => playback.control("stop"),
  /** Seek on the pacing timeline (`wall`, seconds at 1x) when the server reports one; else by sim `t`. */
  seek: (pos: number) => {
    const run = useRunStore.getState().run;
    return playback.control("seek", run.wallDuration > 0 ? { wall: pos } : { t: pos });
  },
  setSpeed: (uiSpeed: number) => {
    const paused = useRunStore.getState().run.status === "paused";
    return playback.control(paused ? "pause" : "resume", { speed: uiToServerSpeed(uiSpeed) });
  },
};
