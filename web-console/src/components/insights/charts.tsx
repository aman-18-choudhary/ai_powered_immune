import { useRef, useState, type ReactNode } from "react";
import { motion } from "framer-motion";
import { formatCi, formatPct } from "@/lib/format";
import { axisMax, delayPoints, formatDelay, linearScale, niceTicks, noAntibodyRecall, stopsHelpingAt, type AblationBar, type DelayPoint } from "@/lib/insights/math";
import type { BenchSensitivity, Metric } from "@/types/api";
import { cn } from "@/lib/utils/cn";

// ---- tooltip plumbing -------------------------------------------------------------------

interface Tip {
  x: number;
  y: number;
  body: ReactNode;
}

function useTip() {
  const wrap = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  const show = (body: ReactNode) => (e: { clientX: number; clientY: number }) => {
    const r = wrap.current?.getBoundingClientRect();
    if (r) setTip({ x: e.clientX - r.left, y: e.clientY - r.top, body });
  };
  const hide = () => setTip(null);
  const node = tip && (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-10 max-w-[240px] rounded-lg border border-line-strong bg-solid px-2.5 py-1.5 text-xs shadow-xl"
      style={{ left: Math.min(tip.x + 12, 9999), top: tip.y + 12 }}
    >
      {tip.body}
    </div>
  );
  return { wrap, show, hide, node };
}

const pct = (v: number | null, d = 1) => formatPct(v, d);
const ciText = (ci: [number, number] | null | undefined, d = 1) => (ci ? `95% CI ${formatCi(ci, d)}` : "no interval");

// ---- headline gauge ---------------------------------------------------------------------

export function RangeGauge({
  label, note, metric, max = 1, digits = 1, tone = "var(--intel)",
}: { label: string; note: string; metric: Metric; max?: number; digits?: number; tone?: string }) {
  const x = linearScale([0, max], [0, 100]);
  const v = metric.value;
  const ci = metric.ci95;
  const ticks = niceTicks(max, 4);
  return (
    <div className="rounded-xl border border-line bg-chip p-3" data-testid={`gauge-${label.toLowerCase().replace(/\W+/g, "-")}`}>
      <div className="text-[10.5px] font-semibold uppercase tracking-wider text-subtle">{label}</div>
      <div className="mono tnum mt-0.5 text-2xl font-semibold" data-testid="gauge-value">
        {pct(v, digits)}
      </div>
      <div className="mono text-[11px] text-muted">{ciText(ci, digits)}</div>
      <div className="relative mt-3 h-2 rounded-full bg-raised" role="img" aria-label={`${label}: ${pct(v, digits)}, ${ciText(ci, digits)}, axis 0 to ${pct(max, 0)}`}>
        {ci && (
          <div
            className="absolute top-0 h-full rounded-full opacity-35"
            style={{ left: `${x(ci[0])}%`, width: `${Math.max(0.8, x(ci[1]) - x(ci[0]))}%`, background: tone }}
          />
        )}
        {v !== null && (
          <motion.div
            className="absolute top-1/2 h-4 w-1.5 -translate-y-1/2 rounded-sm"
            style={{ background: tone }}
            initial={{ left: "0%" }}
            animate={{ left: `${Math.min(99.4, x(v))}%` }}
            transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
          />
        )}
      </div>
      <div className="mono mt-1 flex justify-between text-[9.5px] text-subtle" aria-hidden>
        <span>{pct(ticks[0] ?? 0, 0)}</span>
        <span>{pct(max, max < 0.1 ? 1 : 0)}</span>
      </div>
      <p className="mt-1.5 text-[11px] leading-snug text-muted">{note}</p>
    </div>
  );
}

// ---- grouped bars (ablations) -----------------------------------------------------------

export interface BarGroup {
  key: string;
  label: string;
  bars: AblationBar[];
}

const SERIES_COLORS = ["var(--intel)", "var(--warn)", "var(--flow)", "var(--safe)"];

export function GroupedBars({ groups, height = 280 }: { groups: BarGroup[]; height?: number }) {
  const { wrap, show, hide, node } = useTip();
  const W = 720;
  const m = { l: 40, r: 12, t: 12, b: 44 };
  const innerW = W - m.l - m.r;
  const innerH = height - m.t - m.b;
  const y = linearScale([0, 1], [m.t + innerH, m.t]);
  const series = groups[0]?.bars.map((b) => b.label) ?? [];
  const gw = innerW / Math.max(1, groups.length);
  const bw = Math.min(34, (gw - 28) / Math.max(1, series.length));
  return (
    <div ref={wrap} className="relative">
      <svg viewBox={`0 0 ${W} ${height}`} className="h-auto w-full" role="img" aria-label="Grouped bars: recall by ablation variant" data-testid="ablation-chart">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={m.l} x2={W - m.r} y1={y(t)} y2={y(t)} stroke="var(--border)" />
            <text x={m.l - 6} y={y(t) + 3} textAnchor="end" className="fill-[var(--fg-subtle)] text-[10px]">
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}
        {groups.map((g, gi) => {
          const cx = m.l + gi * gw + gw / 2;
          const x0 = cx - (series.length * bw + (series.length - 1) * 6) / 2;
          return (
            <g key={g.key}>
              {g.bars.map((b, si) => {
                const bx = x0 + si * (bw + 6);
                const v = b.value ?? 0;
                return (
                  <g
                    key={b.variant}
                    onPointerMove={show(
                      <>
                        <div className="font-semibold">{b.label}</div>
                        <div className="mono">
                          {g.label}: {pct(b.value, 1)}
                        </div>
                        <div className="mono text-muted">{ciText(b.ci)}</div>
                      </>,
                    )}
                    onPointerLeave={hide}
                  >
                    <motion.rect
                      x={bx}
                      width={bw}
                      rx={4}
                      fill={SERIES_COLORS[si % SERIES_COLORS.length]}
                      initial={{ y: y(0), height: 0 }}
                      animate={{ y: y(v), height: y(0) - y(v) }}
                      transition={{ duration: 0.8, delay: 0.05 * si, ease: [0.22, 1, 0.36, 1] }}
                    />
                    {b.ci && (
                      <g stroke="var(--fg)" strokeWidth="1.2" opacity="0.75">
                        <line x1={bx + bw / 2} x2={bx + bw / 2} y1={y(b.ci[0])} y2={y(b.ci[1])} />
                        <line x1={bx + bw / 2 - 4} x2={bx + bw / 2 + 4} y1={y(b.ci[1])} y2={y(b.ci[1])} />
                        <line x1={bx + bw / 2 - 4} x2={bx + bw / 2 + 4} y1={y(b.ci[0])} y2={y(b.ci[0])} />
                      </g>
                    )}
                    <text x={bx + bw / 2} y={y(v) - 8} textAnchor="middle" className="mono fill-[var(--fg)] text-[10px] font-semibold">
                      {b.value === null ? "n/a" : (b.value * 100).toFixed(1)}
                    </text>
                  </g>
                );
              })}
              <text x={cx} y={height - 20} textAnchor="middle" className="fill-[var(--fg-muted)] text-[11.5px] font-medium">
                {g.label}
              </text>
            </g>
          );
        })}
      </svg>
      <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label="Legend">
        {series.map((s, i) => (
          <li key={s} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} aria-hidden />
            {s}
          </li>
        ))}
      </ul>
      {node}
    </div>
  );
}

// ---- delay sensitivity ------------------------------------------------------------------

export function DelayChart({ rows, height = 300 }: { rows: BenchSensitivity[]; height?: number }) {
  const { wrap, show, hide, node } = useTip();
  const pts: DelayPoint[] = delayPoints(rows);
  const none = noAntibodyRecall(rows);
  const stop = stopsHelpingAt(pts, none);
  if (pts.length === 0) return <p className="p-4 text-sm text-muted">No sensitivity rows in this benchmark file.</p>;
  const W = 720;
  const m = { l: 48, r: 20, t: 16, b: 52 };
  const innerW = W - m.l - m.r;
  const lows = pts.map((p) => p.lo ?? p.recall);
  const floor = Math.min(...lows, none ?? 1);
  const yMin = Math.max(0, Math.floor((floor - 0.03) * 20) / 20);
  const y = linearScale([yMin, 1], [height - m.b, m.t]);
  const x = (i: number) => m.l + (pts.length === 1 ? innerW / 2 : (i / (pts.length - 1)) * innerW);
  const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)} ${y(p.recall).toFixed(1)}`).join(" ");
  const band =
    pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)} ${y(p.hi ?? p.recall).toFixed(1)}`).join(" ") +
    " " +
    [...pts].reverse().map((p, j) => `L${x(pts.length - 1 - j).toFixed(1)} ${y(p.lo ?? p.recall).toFixed(1)}`).join(" ") +
    " Z";
  const ticks = niceTicks(1 - yMin, 4).map((t) => +(yMin + t).toFixed(4)).filter((t) => t <= 1.0001);
  return (
    <div ref={wrap} className="relative">
      <svg viewBox={`0 0 ${W} ${height}`} className="h-auto w-full" role="img" aria-label="Victim-transfer recall against antibody confirmation delay" data-testid="delay-chart">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={m.l} x2={W - m.r} y1={y(t)} y2={y(t)} stroke="var(--border)" />
            <text x={m.l - 6} y={y(t) + 3} textAnchor="end" className="fill-[var(--fg-subtle)] text-[10px]">
              {(t * 100).toFixed(0)}%
            </text>
          </g>
        ))}
        {none !== null && (
          <g>
            <line x1={m.l} x2={W - m.r} y1={y(none)} y2={y(none)} stroke="var(--warn)" strokeWidth="1.5" strokeDasharray="6 5" />
            <text x={W - m.r} y={y(none) + 14} textAnchor="end" className="fill-[var(--warn)] text-[10.5px] font-medium">
              no antibody: {pct(none, 1)}
            </text>
          </g>
        )}
        <path d={band} fill="var(--intel)" opacity="0.16" />
        <motion.path
          d={line}
          fill="none"
          stroke="var(--intel)"
          strokeWidth="2.5"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 1, ease: "easeOut" }}
        />
        {pts.map((p, i) => {
          const isStop = stop?.delay === p.delay;
          return (
            <g
              key={p.delay}
              onPointerMove={show(
                <>
                  <div className="font-semibold">Confirmation delay {formatDelay(p.delay)}</div>
                  <div className="mono">Victim-transfer recall {pct(p.recall, 1)}</div>
                  <div className="mono text-muted">{p.lo !== null && p.hi !== null ? ciText([p.lo, p.hi]) : "no interval"}</div>
                  {p.matches !== null && <div className="mono text-muted">{p.matches} antibody matches</div>}
                </>,
              )}
              onPointerLeave={hide}
            >
              <circle cx={x(i)} cy={y(p.recall)} r={isStop ? 7 : 5} fill={isStop ? "var(--danger)" : "var(--intel)"} stroke="var(--bg)" strokeWidth="2" />
              <text x={x(i)} y={y(p.recall) - 11} textAnchor="middle" className="mono fill-[var(--fg)] text-[10px] font-semibold">
                {(p.recall * 100).toFixed(1)}
              </text>
              <text x={x(i)} y={height - 28} textAnchor="middle" className="fill-[var(--fg-muted)] text-[11px]">
                {formatDelay(p.delay)}
              </text>
              <rect x={x(i) - 16} y={m.t} width={32} height={height - m.t - m.b} fill="transparent" />
            </g>
          );
        })}
        <text x={m.l + innerW / 2} y={height - 6} textAnchor="middle" className="fill-[var(--fg-subtle)] text-[10.5px]">
          Time from first held transfer to analyst confirmation (steps are not to scale)
        </text>
      </svg>
      <p className={cn("mt-1 text-xs", stop ? "text-danger-text" : "text-muted")} data-testid="delay-takeaway">
        {stop
          ? `The antibody stops helping at ${formatDelay(stop.delay)}: victim-transfer recall falls to the no-antibody level (${pct(none, 1)}).`
          : "The antibody helps at every tested delay in this run."}
        {yMin > 0 && <span className="text-subtle"> The vertical axis starts at {(yMin * 100).toFixed(0)}%.</span>}
      </p>
      {node}
    </div>
  );
}

// ---- hero hard case ---------------------------------------------------------------------

const DECISION_ORDER = ["allow", "step_up", "hold_verify"];
const DECISION_COLOR: Record<string, string> = { allow: "var(--danger)", step_up: "var(--warn)", hold_verify: "var(--safe)" };
const DECISION_NAME: Record<string, string> = { allow: "allowed", step_up: "step-up", hold_verify: "held" };

export function DecisionBar({ title, counts, total }: { title: string; counts: Record<string, number>; total: number }) {
  const keys = [...DECISION_ORDER.filter((k) => k in counts), ...Object.keys(counts).filter((k) => !DECISION_ORDER.includes(k))];
  return (
    <div>
      <div className="mb-1 text-xs font-semibold">{title}</div>
      <div className="flex h-7 overflow-hidden rounded-lg border border-line" role="img" aria-label={`${title}: ${keys.map((k) => `${counts[k]} ${DECISION_NAME[k] ?? k}`).join(", ")} of ${total}`}>
        {keys.map((k) => {
          const n = counts[k] ?? 0;
          if (n === 0) return null;
          return (
            <motion.div
              key={k}
              className="grid place-items-center text-[11px] font-semibold text-[#06090f]"
              style={{ background: DECISION_COLOR[k] ?? "var(--subtle)" }}
              initial={{ width: 0 }}
              animate={{ width: `${(n / Math.max(1, total)) * 100}%` }}
              transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1] }}
            >
              {n}
            </motion.div>
          );
        })}
      </div>
      <ul className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-muted">
        {keys.map((k) => (
          <li key={k} className="mono tnum">
            {counts[k]} {DECISION_NAME[k] ?? k.replace(/_/g, " ")}
          </li>
        ))}
      </ul>
    </div>
  );
}

export { axisMax };
