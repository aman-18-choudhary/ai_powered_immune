import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Loader2, Network, Scissors, Target, Undo2 } from "lucide-react";
import { Panel, EmptyState, Skeleton } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { AnimatedNumber } from "@/lib/motion/AnimatedNumber";
import { endpoints } from "@/lib/api/endpoints";
import { ApiError, friendlyError } from "@/lib/api/client";
import { useRest } from "@/lib/hooks/useRest";
import { edgePath, layoutRing } from "@/lib/geo/ringLayout";
import { formatInr } from "@/lib/format";
import { useMotionPrefs } from "@/lib/motion/prefs";
import type { BestTakedown, RingGraph, RingNode, TakedownResult } from "@/types/api";
import { cn } from "@/lib/utils/cn";

const W = 640;
const H = 300;
const ROLE_LABEL: Record<string, string> = { victim: "Victim", mule: "Mule", cash_out: "Cash-out" };

function NodeShape({ role, r, className }: { role: string; r: number; className?: string }) {
  if (role === "cash_out") return <rect x={-r * 0.8} y={-r * 0.8} width={r * 1.6} height={r * 1.6} transform="rotate(45)" className={className} rx={3} />;
  if (role === "mule") return <rect x={-r} y={-r * 0.8} width={r * 2} height={r * 1.6} rx={6} className={className} />;
  return <circle r={r} className={className} />;
}

function Gauge({ pct, before, after }: { pct: number; before: string; after: string }) {
  const R = 46;
  const C = 2 * Math.PI * R;
  const frac = Math.min(1, Math.max(0, pct / 100));
  return (
    <div className="flex items-center gap-4" data-testid="takedown-gauge">
      <svg width="116" height="116" viewBox="0 0 116 116" role="img" aria-label={`Projected harm reduced by ${pct.toFixed(0)} percent`}>
        <circle cx="58" cy="58" r={R} fill="none" stroke="var(--border-strong)" strokeWidth="9" />
        <motion.circle
          cx="58" cy="58" r={R} fill="none" stroke="var(--safe)" strokeWidth="9" strokeLinecap="round"
          strokeDasharray={C} initial={false} animate={{ strokeDashoffset: C * (1 - frac) }}
          transition={{ type: "spring", stiffness: 90, damping: 18 }} transform="rotate(-90 58 58)"
        />
        <text x="58" y="56" textAnchor="middle" className="mono" fontSize="22" fontWeight="600" fill="var(--fg)">
          <tspan>{pct.toFixed(0)}%</tspan>
        </text>
        <text x="58" y="74" textAnchor="middle" fontSize="10" fill="var(--fg-muted)">
          harm cut
        </text>
      </svg>
      <dl className="space-y-1.5 text-xs">
        <div>
          <dt className="text-subtle">Projected harm before</dt>
          <dd className="mono tnum text-sm font-semibold text-danger-text">{formatInr(before, { paise: "never" })}</dd>
        </div>
        <div>
          <dt className="text-subtle">After takedown</dt>
          <dd className="mono tnum text-sm font-semibold text-safe">{formatInr(after, { paise: "never" })}</dd>
        </div>
      </dl>
    </div>
  );
}

export function RingPanel({ refreshKey }: { refreshKey: number }) {
  const { reduced } = useMotionPrefs();
  const graph = useRest<RingGraph>((s) => endpoints.ringGraph("current", s), refreshKey, 400);
  const [k, setK] = useState(1);
  const [selected, setSelected] = useState<string[]>([]);
  const [sim, setSim] = useState<TakedownResult | null>(null);
  const [simErr, setSimErr] = useState<string | null>(null);
  const [simBusy, setSimBusy] = useState(false);
  const [best, setBest] = useState<BestTakedown | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const g = graph.data;

  const layout = useMemo(() => (g ? layoutRing(g.nodes, g.edges, W, H) : []), [g]);
  const pos = useMemo(() => new Map(layout.map((p) => [p.id, p])), [layout]);
  const nodeById = useMemo(() => new Map((g?.nodes ?? []).map((n) => [n.id, n])), [g]);

  // Reset interaction when the ring identity changes.
  const ringId = g?.ring_id;
  useEffect(() => {
    setSelected([]);
    setSim(null);
    setBest(null);
  }, [ringId]);

  // Simulate whenever k or the selection changes.
  const seq = useRef(0);
  useEffect(() => {
    if (!g) return;
    const my = ++seq.current;
    const ctl = new AbortController();
    const h = setTimeout(async () => {
      setSimBusy(true);
      try {
        const body = selected.length > 0 ? { remove: selected } : { k };
        const r = await endpoints.ringSimulate("current", body, ctl.signal);
        if (my === seq.current) {
          setSim(r);
          setSimErr(null);
        }
      } catch (e) {
        if (e instanceof ApiError && e.code === "aborted") return;
        if (my === seq.current) setSimErr(friendlyError(e));
      } finally {
        if (my === seq.current) setSimBusy(false);
      }
    }, 220);
    return () => {
      clearTimeout(h);
      ctl.abort();
    };
  }, [g, k, selected]);

  const removed = new Set(sim?.removed ?? selected);
  const cut = new Set((sim?.cut_edge_list ?? []).map((e) => `${e.src}>${e.dst}`));

  const toggle = (n: RingNode) => {
    if (n.role === "victim") return;
    setBest(null);
    setSelected((s) => (s.includes(n.id) ? s.filter((x) => x !== n.id) : [...s, n.id].slice(-5)));
  };

  const findBest = async () => {
    try {
      const b = await endpoints.bestTakedown("current", k);
      setBest(b);
      setSelected(b.greedy.removed.slice(0, 5));
    } catch (e) {
      setSimErr(friendlyError(e));
    }
  };

  const content = () => {
    if (graph.forbidden) return <EmptyState icon={<Network className="h-5 w-5" aria-hidden />} title="Not available for this role" hint="Ring graphs are an officer, analyst and admin view." />;
    if (!g && graph.loading) return <div className="space-y-3 p-4"><Skeleton className="h-48 w-full" /><Skeleton className="h-12 w-2/3" /></div>;
    if (!g && graph.error) {
      return (
        <EmptyState icon={<Network className="h-5 w-5" aria-hidden />} title={graph.error} hint="The ring graph appears once the scenario has produced transactions.">
          <Button size="sm" variant="secondary" onClick={graph.reload}>Retry</Button>
        </EmptyState>
      );
    }
    if (!g || g.nodes.length === 0) {
      return <EmptyState icon={<Network className="h-5 w-5" aria-hidden />} title="No ring yet" hint="Start the scenario; the ring forms as mule payments arrive." />;
    }
    return (
      <div className="grid gap-4 p-3 lg:grid-cols-[1.5fr_1fr]">
        <div className="min-w-0">
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="group" aria-label="Ring graph: victims to mules to cash-out">
            <g fontSize="10" fill="var(--fg-subtle)" textAnchor="middle" aria-hidden>
              {[...new Set(layout.map((p) => p.tier))].map((t) => {
                const n = layout.find((p) => p.tier === t);
                const role = g.nodes.find((x) => x.tier === t)?.role ?? "";
                return n ? (
                  <text key={t} x={n.x} y={12}>
                    {ROLE_LABEL[role] ?? role}
                  </text>
                ) : null;
              })}
            </g>
            <g fill="none">
              {g.edges.map((e) => {
                const a = pos.get(e.src);
                const b = pos.get(e.dst);
                if (!a || !b) return null;
                const isCut = cut.has(`${e.src}>${e.dst}`);
                const flagged = e.flagged > 0;
                return (
                  <g key={`${e.src}>${e.dst}`}>
                    <path
                      d={edgePath(a, b)}
                      stroke={isCut ? "var(--danger)" : flagged ? "var(--warn)" : "var(--flow)"}
                      strokeWidth={isCut ? 2.2 : 1.6}
                      strokeOpacity={isCut ? 0.95 : 0.7}
                      className={!reduced && !isCut ? "flow-dash" : undefined}
                      strokeDasharray={isCut ? "2 5" : undefined}
                    />
                  </g>
                );
              })}
            </g>
            <g>
              {g.nodes.map((n) => {
                const p = pos.get(n.id);
                if (!p) return null;
                const isRemoved = removed.has(n.id);
                const canPick = n.role !== "victim";
                const stroke = isRemoved ? "var(--danger)" : n.role === "cash_out" ? "var(--danger)" : n.role === "mule" ? "var(--warn)" : "var(--flow)";
                return (
                  <g
                    key={n.id}
                    transform={`translate(${p.x},${p.y})`}
                    tabIndex={canPick ? 0 : -1}
                    role={canPick ? "button" : "img"}
                    aria-pressed={canPick ? selected.includes(n.id) : undefined}
                    aria-label={`${ROLE_LABEL[n.role] ?? n.role} ${n.id}${isRemoved ? ", frozen in simulation" : ""}${canPick ? ". Press to toggle freeze" : ""}`}
                    onClick={() => toggle(n)}
                    onKeyDown={(e) => {
                      if (canPick && (e.key === "Enter" || e.key === " ")) {
                        e.preventDefault();
                        toggle(n);
                      }
                    }}
                    onMouseEnter={() => setHover(n.id)}
                    onMouseLeave={() => setHover(null)}
                    onFocus={() => setHover(n.id)}
                    onBlur={() => setHover(null)}
                    className={cn("outline-none", canPick && "cursor-pointer")}
                    opacity={isRemoved ? 0.65 : 1}
                  >
                    <NodeShape role={n.role} r={15} className="fill-[var(--surface-solid)]" />
                    <NodeShape role={n.role} r={15} className="fill-none" />
                    <g style={{ stroke, strokeWidth: isRemoved ? 3 : 2 }}>
                      <NodeShape role={n.role} r={15} className="fill-none" />
                    </g>
                    <text textAnchor="middle" y={4} fontSize="11" fontWeight="600" fill="var(--fg)">
                      {isRemoved ? "✕" : n.role === "victim" ? "V" : n.role === "mule" ? "M" : "₹"}
                    </text>
                    <text textAnchor="middle" y={32} fontSize="9.5" className="mono" fill="var(--fg-muted)">
                      {n.id.slice(0, 6)}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
          <div className="mt-1 min-h-[34px] text-[11.5px] text-muted" aria-live="polite">
            {hover && nodeById.get(hover) ? (
              <NodeInfo n={nodeById.get(hover) as RingNode} />
            ) : (
              <span>
                Click mule or cash-out nodes to freeze them (up to 5). {g.victims} victims, {g.nodes.length - g.victims >= 0 ? g.members.length : 0} ring
                accounts.
              </span>
            )}
          </div>
        </div>

        <div className="space-y-3">
          <div className="rounded-xl border border-line bg-chip p-3">
            <label htmlFor="freeze-k" className="flex items-center justify-between text-xs font-medium">
              <span className="flex items-center gap-1.5">
                <Scissors className="h-3.5 w-3.5 text-danger-text" aria-hidden /> Freeze k accounts
              </span>
              <span className="mono tnum text-sm font-semibold">{selected.length > 0 ? selected.length : k}</span>
            </label>
            <input
              id="freeze-k"
              type="range"
              min={1}
              max={5}
              step={1}
              value={k}
              disabled={selected.length > 0}
              onChange={(e) => {
                setBest(null);
                setK(Number(e.target.value));
              }}
              className="mt-2 w-full accent-[var(--intel-strong)] disabled:opacity-40"
            />
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="intel" onClick={findBest} data-testid="best-takedown">
                <Target className="h-3.5 w-3.5" aria-hidden /> Best takedown
              </Button>
              <Button size="sm" variant="ghost" disabled={selected.length === 0} onClick={() => { setSelected([]); setBest(null); }}>
                <Undo2 className="h-3.5 w-3.5" aria-hidden /> Clear picks
              </Button>
            </div>
            {best && (
              <p className="mt-2 text-[11px] text-muted">
                Greedy picked {best.greedy.removed.length} {best.greedy.removed.length === 1 ? "account" : "accounts"}
                {best.gap_pct != null ? `; gap to optimal ${best.gap_pct.toFixed(1)}%` : ""}.
              </p>
            )}
          </div>

          <div className="relative min-h-[120px]">
            {sim ? (
              <Gauge pct={sim.reduction_pct} before={sim.harm_before} after={sim.harm_after} />
            ) : simErr ? (
              <p role="alert" className="text-xs text-danger-text">{simErr}</p>
            ) : (
              <Skeleton className="h-[116px] w-full" />
            )}
            {sim && (
              <p className="mt-2 text-[11.5px] text-muted">
                <span className="mono tnum font-semibold text-fg">
                  <AnimatedNumber value={sim.cut_edges} />
                </span>{" "}
                payment edges cut
                {simBusy && <Loader2 className="ml-2 inline h-3 w-3 animate-spin" aria-hidden />}
              </p>
            )}
          </div>
          <p className="rounded-lg border border-line bg-chip px-2.5 py-1.5 text-[11px] leading-snug text-subtle">
            Model projection from this run&apos;s transactions, not a prediction. It assumes the observed rate continues and nobody re-routes
            after a freeze.
          </p>
        </div>
      </div>
    );
  };

  return (
    <Panel
      title="Ring graph and takedown simulator"
      icon={<Network className="h-3.5 w-3.5" aria-hidden />}
      className="h-full"
      actions={g ? <span className="mono text-[10.5px] text-subtle">ring {g.ring_id.slice(0, 8)}</span> : null}
    >
      {content()}
    </Panel>
  );
}

function NodeInfo({ n }: { n: RingNode }) {
  return (
    <span>
      <strong className="text-fg">{ROLE_LABEL[n.role] ?? n.role}</strong> <span className="mono">{n.id}</span>
      {n.bank ? <> · {n.bank.replace("bank_", "").toUpperCase()}</> : null} · in {formatInr(n.in_inr, { paise: "never" })} · out{" "}
      {formatInr(n.out_inr, { paise: "never" })}
      {n.flags.length > 0 && <> · {n.flags.join(", ").replace(/_/g, " ")}</>}
    </span>
  );
}
