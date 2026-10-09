import { useEffect, useRef, useState } from "react";
import { formatSimClock } from "@/lib/format";
import { STEP_ORDER, STEP_SHORT } from "./notes";
import { cn } from "@/lib/utils/cn";

export interface Marker {
  key: string;
  t: number;
  title: string;
}

interface Props {
  t: number;
  duration: number;
  markers: Marker[];
  reached: Set<string>;
  currentKey: string | null;
  disabled?: boolean;
  onSeek: (t: number) => void;
}

export function Timeline({ t, duration, markers, reached, currentKey, disabled, onSeek }: Props) {
  const [drag, setDrag] = useState<number | null>(null);
  const dragging = useRef(false);
  const shown = drag ?? t;
  const pct = duration > 0 ? Math.min(100, Math.max(0, (shown / duration) * 100)) : 0;

  useEffect(() => {
    if (!dragging.current) setDrag(null);
  }, [t]);

  const commit = () => {
    if (drag !== null) onSeek(drag);
    dragging.current = false;
    setDrag(null);
  };

  const byKey = new Map(markers.map((m) => [m.key, m]));
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <span className="mono tnum w-12 text-right text-xs text-muted" aria-hidden>
          {formatSimClock(shown)}
        </span>
        <div className="relative h-8 flex-1">
          <div className="absolute left-0 right-0 top-1/2 h-1.5 -translate-y-1/2 overflow-hidden rounded-full bg-chip">
            <div
              className="h-full rounded-full bg-[linear-gradient(90deg,var(--intel-strong),var(--flow))]"
              style={{ width: `${pct}%`, transition: dragging.current ? "none" : "width 200ms linear" }}
            />
          </div>
          {markers.map((m) =>
            duration > 0 ? (
              <button
                key={m.key}
                type="button"
                disabled={disabled}
                onClick={() => onSeek(m.t)}
                title={`${m.title} · ${formatSimClock(m.t)}`}
                aria-label={`Jump to ${m.title}`}
                className={cn(
                  "absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-bg transition-transform hover:scale-125",
                  m.key === currentKey ? "bg-flow" : reached.has(m.key) ? "bg-intel" : "bg-subtle",
                )}
                style={{ left: `${(m.t / duration) * 100}%` }}
              />
            ) : null,
          )}
          <input
            type="range"
            min={0}
            max={Math.max(1, Math.round(duration))}
            step={1}
            value={Math.round(shown)}
            disabled={disabled || duration <= 0}
            aria-label="Scenario timeline (simulated seconds)"
            aria-valuetext={`${formatSimClock(shown)} of ${formatSimClock(duration)}`}
            onChange={(e) => {
              dragging.current = true;
              setDrag(Number(e.target.value));
            }}
            onPointerUp={commit}
            onKeyUp={(e) => {
              if (e.key.startsWith("Arrow") || e.key === "Home" || e.key === "End" || e.key.startsWith("Page")) commit();
            }}
            className="absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed"
          />
        </div>
        <span className="mono tnum w-12 text-xs text-muted" aria-hidden>
          {formatSimClock(duration)}
        </span>
      </div>
      <ol className="flex flex-wrap gap-1.5" aria-label="Scenario steps">
        {STEP_ORDER.map((k, i) => {
          const done = reached.has(k) || (k === "bank_b_blocks" && reached.has("bank_b_misses"));
          const m = byKey.get(k) ?? (k === "bank_b_blocks" ? byKey.get("bank_b_misses") : undefined);
          const current = currentKey === k || (k === "bank_b_blocks" && currentKey === "bank_b_misses");
          return (
            <li key={k}>
              <button
                type="button"
                disabled={disabled || !m}
                onClick={() => m && onSeek(m.t)}
                aria-current={current ? "step" : undefined}
                className={cn(
                  "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors disabled:cursor-default",
                  current
                    ? "border-flow bg-flow-soft text-fg"
                    : done
                      ? "border-intel/40 bg-intel-soft text-fg"
                      : "border-line bg-chip text-subtle",
                )}
              >
                <span className="mono tnum text-[10px] opacity-70">{i + 1}</span>
                {k === "bank_b_blocks" && reached.has("bank_b_misses") ? STEP_SHORT.bank_b_misses : STEP_SHORT[k]}
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
