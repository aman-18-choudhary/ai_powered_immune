import { endpoints } from "@/lib/api/endpoints";
import { friendlyError } from "@/lib/api/client";
import { toast } from "./toasts";
import { useRunStore } from "./runStore";

/** UI speed 1x = 4 simulated seconds per wall second (server range 0.25..20). */
export const SPEED_BASE = 4;
export const SPEED_OPTIONS = [0.5, 1, 2, 5] as const;
export const DEFAULT_UI_SPEED = 5;

export function uiToServerSpeed(m: number): number {
  return Math.min(20, Math.max(0.25, +(m * SPEED_BASE).toFixed(2)));
}
export function serverToUiSpeed(s: number): number {
  return +(s / SPEED_BASE).toFixed(2);
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
  async start(uiSpeed = DEFAULT_UI_SPEED, seed = 1) {
    const r = await guarded("Could not start the scenario", () =>
      endpoints.demoStart({ seed, speed: uiToServerSpeed(uiSpeed), mode: "hero" }),
    );
    if (r) {
      const st = await guarded("Could not read run state", () => endpoints.demoState());
      if (st) useRunStore.getState().mergeState(st);
    }
    return r;
  },
  async control(action: "pause" | "resume" | "stop" | "seek", extra: { t?: number; speed?: number } = {}) {
    const st = await guarded("Playback control failed", () => endpoints.demoControl({ action, ...extra }));
    if (st) useRunStore.getState().mergeState(st);
    return st;
  },
  pause: () => playback.control("pause"),
  resume: () => playback.control("resume"),
  stop: () => playback.control("stop"),
  seek: (t: number) => playback.control("seek", { t }),
  setSpeed: (uiSpeed: number) => {
    const paused = useRunStore.getState().run.status === "paused";
    return playback.control(paused ? "pause" : "resume", { speed: uiToServerSpeed(uiSpeed) });
  },
};
