import { create } from "zustand";
import type { DemoState, StreamEvent } from "@/types/api";
import type { ConnState } from "@/lib/ws/stream";
import { applyEvents, initialRun, type RunState } from "./reducer";

interface Clock {
  /** Sim time at `at`. */
  t: number;
  speed: number;
  running: boolean;
  /** performance.now() when t was observed. */
  at: number;
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
  clock: { t: 0, speed: 1, running: false, at: now() },
  apply: (events) => {
    const run = applyEvents(get().run, events);
    set({
      run,
      clock: {
        t: run.t,
        speed: run.speed,
        running: run.status === "running" || run.status === "starting",
        at: now(),
      },
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
    };
    set({
      run: next,
      clock: { t: st.t, speed: st.speed, running: status === "running" || status === "starting", at: now() },
    });
  },
  reset: () => set({ run: initialRun(), clock: { t: 0, speed: 1, running: false, at: now() } }),
}));
