import { useEffect, useRef } from "react";
import { animate, useMotionValue } from "framer-motion";
import { useMotionPrefs } from "./prefs";

interface Props {
  value: number;
  format?: (n: number) => string;
  className?: string;
  /** Used in tests / reduced motion: render the final text immediately. */
  duration?: number;
}

/** Spring-animated counter. Writes text straight to the DOM node (no React re-render per frame). */
export function AnimatedNumber({ value, format = (n) => String(Math.round(n)), className, duration }: Props) {
  const { reduced } = useMotionPrefs();
  const ref = useRef<HTMLSpanElement>(null);
  const mv = useMotionValue(value);
  const fmt = useRef(format);
  fmt.current = format;

  useEffect(() => {
    if (reduced) {
      mv.set(value);
      if (ref.current) ref.current.textContent = fmt.current(value);
      return;
    }
    const controls = animate(mv, value, duration ? { duration } : { type: "spring", stiffness: 140, damping: 24, mass: 0.9 });
    return () => controls.stop();
  }, [value, reduced, mv, duration]);

  useEffect(() => {
    const unsub = mv.on("change", (v) => {
      if (ref.current) ref.current.textContent = fmt.current(v);
    });
    return unsub;
  }, [mv]);

  return (
    <span ref={ref} className={className} data-value={value}>
      {format(value)}
    </span>
  );
}
