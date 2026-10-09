import { useEffect, useState } from "react";
import { useRunStore } from "./runStore";

/** Smooth simulated clock (seconds): last observed t advanced at the run speed. Updates ~10 Hz. */
export function useSimTime(intervalMs = 100): number {
  const [t, setT] = useState(() => useRunStore.getState().run.t);
  useEffect(() => {
    const read = () => {
      const { clock, run } = useRunStore.getState();
      const dt = clock.running ? ((typeof performance !== "undefined" ? performance.now() : Date.now()) - clock.at) / 1000 : 0;
      const v = clock.t + dt * clock.speed;
      setT(run.duration > 0 ? Math.min(run.duration, v) : v);
    };
    read();
    const h = setInterval(read, intervalMs);
    return () => clearInterval(h);
  }, [intervalMs]);
  return t;
}
