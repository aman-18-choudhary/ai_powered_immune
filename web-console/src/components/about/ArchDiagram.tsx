import { useState } from "react";
import { ARCH_EDGES, ARCH_NODES, edgesOf, nodeById, type ArchNode } from "@/lib/about/architecture";
import { cn } from "@/lib/utils/cn";

const TONE: Record<ArchNode["tone"], { stroke: string; fill: string; label: string }> = {
  real: { stroke: "var(--intel)", fill: "var(--intel-soft)", label: "Real service code" },
  sim: { stroke: "var(--warn)", fill: "var(--warn-soft)", label: "Simulated input" },
  ui: { stroke: "var(--flow)", fill: "var(--flow-soft)", label: "Delivery and UI" },
};

/** Animated architecture diagram. Hover or focus a block to light up its flows and read what it is. */
export function ArchDiagram() {
  const [active, setActive] = useState<string | null>(null);
  const hot = new Set(active ? edgesOf(active).map((e) => e.id) : []);
  const info = active ? nodeById(active) : null;
  return (
    <div>
      <svg viewBox="0 0 900 450" className="h-auto w-full" role="group" aria-label="Architecture diagram: services, event bus, threat memory and ledger" data-testid="arch">
        <defs>
          <marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0 0 L10 5 L0 10 z" fill="currentColor" />
          </marker>
        </defs>
        {ARCH_EDGES.map((e) => {
          const on = hot.has(e.id);
          return (
            <path
              key={e.id}
              d={e.d}
              fill="none"
              markerEnd="url(#arr)"
              className={cn("flow-dash transition-colors", on ? "text-[var(--flow)]" : "text-[var(--border-strong)]")}
              stroke="currentColor"
              strokeWidth={on ? 3 : 1.6}
              data-edge={e.id}
              data-hot={on ? "1" : "0"}
            />
          );
        })}
        {ARCH_NODES.map((n) => {
          const tone = TONE[n.tone];
          const on = active === n.id;
          const isBus = n.id === "bus";
          return (
            <g
              key={n.id}
              tabIndex={0}
              role="button"
              aria-pressed={on}
              aria-label={`${n.label}. ${n.desc}`}
              onPointerEnter={() => setActive(n.id)}
              onPointerLeave={() => setActive(null)}
              onFocus={() => setActive(n.id)}
              onBlur={() => setActive(null)}
              className="cursor-pointer outline-none"
              data-node={n.id}
            >
              <rect
                x={n.x} y={n.y} width={n.w} height={n.h} rx={14}
                fill={tone.fill}
                stroke={tone.stroke}
                strokeWidth={on ? 3 : 1.5}
                strokeDasharray={n.tone === "sim" ? "6 5" : undefined}
              />
              <text x={n.x + n.w / 2} y={n.y + (isBus ? n.h / 2 + 5 : n.h / 2 - 3)} textAnchor="middle" className="fill-[var(--fg)] text-[14px] font-semibold">
                {n.label}
              </text>
              {!isBus && (
                <text x={n.x + n.w / 2} y={n.y + n.h / 2 + 16} textAnchor="middle" className="fill-[var(--fg-muted)] text-[11.5px]">
                  {n.sub}
                </text>
              )}
            </g>
          );
        })}
        {ARCH_EDGES.filter((e) => hot.has(e.id)).map((e) => {
          // label near the middle of the edge's first segment
          const m = e.d.match(/M(\d+) (\d+)/);
          const x = m ? Number(m[1]) : 0;
          const y = m ? Number(m[2]) : 0;
          return (
            <text key={`l-${e.id}`} x={x + 8} y={(y + 200) / 2 + (y < 200 ? 0 : 60)} className="fill-[var(--flow)] text-[11px] font-semibold">
              {e.label}
            </text>
          );
        })}
      </svg>
      <div className="mt-2 min-h-[64px] rounded-xl border border-line bg-chip p-3 text-sm" aria-live="polite" data-testid="arch-info">
        {info ? (
          <>
            <p className="font-semibold">
              {info.label} <span className="ml-1 text-xs font-normal text-muted">{TONE[info.tone].label}</span>
            </p>
            <p className="text-muted">{info.desc}</p>
          </>
        ) : (
          <p className="text-muted">Hover or focus a block to see what it is and which flows touch it.</p>
        )}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" aria-label="Legend">
        {(Object.keys(TONE) as ArchNode["tone"][]).map((k) => (
          <li key={k} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-4 rounded-sm border" style={{ borderColor: TONE[k].stroke, background: TONE[k].fill, borderStyle: k === "sim" ? "dashed" : "solid" }} aria-hidden />
            {TONE[k].label}
          </li>
        ))}
      </ul>
    </div>
  );
}
