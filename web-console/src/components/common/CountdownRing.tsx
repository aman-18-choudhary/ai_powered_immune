import { clamp01, countdown } from "@/lib/format/countdown";
import { cn } from "@/lib/utils/cn";

interface Props {
  /** Simulated seconds remaining until the hold deadline, and the total window. */
  remaining: number;
  total: number;
  size?: number;
  state?: "open" | "resolved" | "overdue";
  className?: string;
}

/** SVG countdown ring. The arc is driven by the same sim clock as the rest of the UI. */
export function CountdownRing({ remaining, total, size = 44, state = "open", className }: Props) {
  const r = (size - 6) / 2;
  const c = 2 * Math.PI * r;
  const frac = state === "resolved" ? 1 : clamp01(total > 0 ? remaining / total : 0);
  const overdue = state === "overdue" || (state === "open" && remaining <= 0);
  const tone = state === "resolved" ? "var(--safe)" : overdue ? "var(--danger)" : frac < 0.34 ? "var(--warn)" : "var(--flow)";
  return (
    <div className={cn("relative grid place-items-center", className)} style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--border-strong)" strokeWidth="3" />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke={tone} strokeWidth="3" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - frac)}
          style={{ transition: "stroke-dashoffset 400ms linear, stroke 200ms" }}
        />
      </svg>
      <span className="mono absolute text-[10px] font-medium tnum">
        {state === "resolved" ? "✓" : overdue ? "0s" : countdown(remaining)}
      </span>
    </div>
  );
}
