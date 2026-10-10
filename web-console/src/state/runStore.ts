import { create } from "zustand";
import type { DemoState, StreamEvent } from "@/types/api";
import type { ConnState } from "@/lib/ws/stream";
import { applyEvents, initialRun, type RunState } from "./reducer";

export interface Clock {
  /** Sim time at `at`. */
  t: number;
  /** Position on the pacing timeline (seconds at 1x) at `at`. */
  w: number;
  /** Server speed multiplier: the timeline advances `speed` seconds per real second. */
  speed: number;
  /** Simulated seconds per timeline second around `w` (1 under real pacing). */
  rate: number;
  running: boolean;
  /** performance.now() when t was observed. */
  at: number;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Next clock sample; the sim-time rate is re-estimated from consecutive samples. */
export function nextClock(prev: Clock, t: number, w: number, speed: number, running: boolean, at: number): Clock {
  let rate = prev.rate;
  const dw = w - prev.w;
  if (dw > 0.25) rate = clamp((t - prev.t) / dw, 0, 5000);
  else if (dw < -0.25) rate = 1;
  return { t, w, speed, rate, running, at };
}

interface RunStore {
  run: RunState;
  conn: ConnState;
  /** Resync counter: bumps whenever a gap was detected. */
  resyncs: number;
  clock: Clock;
  apply: (events: StreamEvent[]) => void;
  setConn: (c: ConnState) => void;
  noteResync: () => void;
  mergeState: (s: DemoState) => void;
  reset: () => void;
}

const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

export const useRunStore = create<RunStore>((set, get) => ({
  run: initialRun(),
  conn: "idle",
  resyncs: 0,
  clock: { t: 0, w: 0, speed: 1, rate: 1, running: false, at: now() },
  apply: (events) => {
    const run = applyEvents(get().run, events);
    set({
      run,
      clock: nextClock(
        get().clock,
        run.t,
        run.wallOffset,
        run.speed,
        run.status === "running" || run.status === "starting",
        now(),
      ),
    });
  },
  setConn: (conn) => set({ conn }),
  noteResync: () => set((s) => ({ resyncs: s.resyncs + 1 })),
  mergeState: (st) => {
    const { run } = get();
    const status = st.status;
    const next: RunState = {
      ...run,
      status,
      speed: st.speed || run.speed,
      duration: st.duration || run.duration,
      step: st.step ?? run.step,
      t: st.t,
      runId: st.run_id ?? run.runId,
      banks: st.banks ?? run.banks,
      seed: st.seed ?? run.seed,
      mode: st.mode ?? run.mode,
      scenarioNote: st.scenario_note ?? run.scenarioNote,
      pacing: st.pacing ?? run.pacing,
      wallDuration: st.wall_duration ?? run.wallDuration,
      wallOffset: st.wall_offset ?? run.wallOffset,
    };
    set({
      run: next,
      clock: nextClock(get().clock, st.t, next.wallOffset, st.speed || run.speed, status === "running" || status === "starting", now()),
    });
  },
  reset: () => set({ run: initialRun(), clock: { t: 0, w: 0, speed: 1, rate: 1, running: false, at: now() } }),
}));
