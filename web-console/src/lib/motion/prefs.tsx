import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { MotionConfig } from "framer-motion";

export type MotionPref = "system" | "reduced" | "full";
interface MotionCtx {
  /** true when motion must be minimised (system preference or in-app toggle). */
  reduced: boolean;
  pref: MotionPref;
  setReduced: (v: boolean) => void;
}
const Ctx = createContext<MotionCtx>({ reduced: false, pref: "system", setReduced: () => {} });
const KEY = "ssi.motion";

function readPref(): MotionPref {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "reduced" || v === "full") return v;
  } catch {
    /* storage unavailable */
  }
  return "system";
}

function systemReduced(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

export function MotionPrefsProvider({ children }: { children: ReactNode }) {
  const [pref, setPref] = useState<MotionPref>(readPref);
  const [sys, setSys] = useState(systemReduced);

  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!mq) return;
    const on = () => setSys(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  const reduced = pref === "reduced" || (pref === "system" && sys);

  useEffect(() => {
    document.documentElement.dataset.motion = reduced ? "reduced" : "full";
  }, [reduced]);

  const setReduced = useCallback((v: boolean) => {
    const next: MotionPref = v ? "reduced" : "full";
    setPref(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* ignore */
    }
  }, []);

  const value = useMemo(() => ({ reduced, pref, setReduced }), [reduced, pref, setReduced]);
  return (
    <Ctx.Provider value={value}>
      <MotionConfig reducedMotion={reduced ? "always" : "never"}>{children}</MotionConfig>
    </Ctx.Provider>
  );
}

export function useMotionPrefs(): MotionCtx {
  return useContext(Ctx);
}
