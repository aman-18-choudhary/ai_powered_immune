import { Suspense, lazy, useMemo } from "react";
import { Map as MapIcon } from "lucide-react";
import { KpiStrip, type KpiValues } from "@/components/command/KpiStrip";
import { IncidentFeed } from "@/components/command/IncidentFeed";
import { AntibodyTicker } from "@/components/command/AntibodyTicker";
import { RingPanel } from "@/components/command/RingPanel";
import { BenchmarkMini } from "@/components/command/BenchmarkMini";
import { Panel, Skeleton } from "@/components/common/Panel";
import { TrendIcon } from "@/components/common/IndiaMap";
import { endpoints } from "@/lib/api/endpoints";
import { useRest } from "@/lib/hooks/useRest";
import { inrToPaise } from "@/lib/format";
import { useRunStore } from "@/state/runStore";
import { selectKpis, selectTimeToBlock } from "@/state/reducer";
import { aggregateCities } from "@/lib/geo/arcs";
import type { AntibodiesResponse, GeoResponse, HoldsResponse } from "@/types/api";

const IndiaMap = lazy(() => import("@/components/common/IndiaMap"));

export default function Command() {
  const run = useRunStore((s) => s.run);
  const kpi = useMemo(() => selectKpis(run), [run]);
  const ttb = useMemo(() => selectTimeToBlock(run), [run]);

  const holdKey = `${run.holdOrder.length}:${Object.values(run.holds).map((h) => h.state[0]).join("")}`;
  const holds = useRest<HoldsResponse>((s) => endpoints.holds("all", s), holdKey, 300);
  const abKey = run.antibodies.length * 100 + run.antibodies.reduce((n, a) => n + a.applied.length, 0);
  const abs = useRest<AntibodiesResponse>((s) => endpoints.antibodies("active", s), abKey, 300);
  const geo = useRest<GeoResponse>((s) => endpoints.geo(s), run.geo.length, 400);
  const ringKey = Object.values(run.rings).reduce((n, r) => n + r.members * 10 + r.victims, 0) + run.holdOrder.length;

  const money = useMemo(() => {
    if (!holds.data) return { rupees: null as number | null, exact: null as string | null };
    let total = 0n;
    for (const h of holds.data.holds) {
      if (h.state === "released") continue;
      const p = inrToPaise(h.amount_inr);
      if (p !== null) total += p;
    }
    return { rupees: Number(total / 100n), exact: `${total / 100n}.${(total % 100n).toString().padStart(2, "0")}` };
  }, [holds.data]);

  const latency = run.antibodies.flatMap((a) => a.applied.map((x) => x.latency_ms))[0] ?? null;

  const values: KpiValues = {
    callsFlagged: kpi.callsFlagged,
    transfersHeld: kpi.transfersHeld,
    protectedRupees: money.rupees,
    protectedExact: money.exact,
    antibodiesActive: abs.data ? abs.data.antibodies.length : kpi.antibodiesPublished,
    medianTimeToBlockS: ttb,
    antibodyLatencyMs: kpi.medianLatencyMs ?? latency,
    ledgerEntries: kpi.ledgerEntries,
  };

  const points = useMemo(() => run.geo.map((g) => ({ lat: g.lat, lon: g.lon, kind: g.kind, city: g.city, state: g.state, t: g.t })), [run.geo]);
  const hotspots = useMemo(() => geo.data?.hotspots ?? [], [geo.data]);
  // Event totals per city come from the stream; the API supplies the trend for the latest window.
  const cityRows = useMemo(() => {
    const trend = new Map(hotspots.map((h) => [h.city, h]));
    return aggregateCities(points)
      .filter((c) => (c.kinds.cash_out ?? 0) < c.total)
      .sort((a, b) => b.total - a.total)
      .map((c) => ({ city: c.city, total: c.total, trend: trend.get(c.city) }));
  }, [points, hotspots]);
  const otherBank = "bank_b";

  return (
    <div className="mx-auto w-full min-w-0 max-w-[1680px] space-y-3 px-4 py-4 md:px-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Command Centre</h1>
          <p className="text-sm text-muted">
            {run.hasData
              ? run.status === "finished"
                ? "Run finished. The final state of the simulated campaign."
                : "Live: updating as the simulated campaign unfolds."
              : "No run yet. Start one from the Live page; this view fills in as events arrive."}
          </p>
        </div>
        <span className="mono tnum text-xs text-subtle">
          {run.hasData ? `${run.counters.txns_scored} scored transactions` : ""}
        </span>
      </div>

      <KpiStrip v={values} loadingMoney={holds.loading} />

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
        <div className="min-w-0 xl:col-span-7">
          <Panel
            title="India: hotspots and payment paths"
            icon={<MapIcon className="h-3.5 w-3.5" aria-hidden />}
            className="h-full"
            bodyClassName="grid min-h-[420px] grid-cols-1 gap-3 p-3 md:grid-cols-[1fr_190px]"
            aria-label="India map"
          >
            <div className="relative min-h-[380px]">
              <Suspense fallback={<Skeleton className="h-full min-h-[380px] w-full" />}>
                <IndiaMap points={points} hotspots={hotspots} className="h-full" />
              </Suspense>
            </div>
            <div className="space-y-2 text-xs">
              <h3 className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-subtle">Hotspots (events, trend)</h3>
              {cityRows.length === 0 ? (
                <p className="text-subtle">{geo.forbidden ? "Not available for this role." : "Waiting for events…"}</p>
              ) : (
                <ul className="space-y-1.5">
                  {cityRows.map((h) => (
                    <li key={h.city} className="flex items-center justify-between rounded-lg border border-line bg-chip px-2 py-1.5">
                      <span className="font-medium">{h.city}</span>
                      <span className="flex items-center gap-1 text-muted" title={h.trend ? `Last window ${h.trend.count}, previous ${h.trend.prev_count}` : undefined}>
                        <span className="mono tnum text-fg">{h.total}</span>
                        <TrendIcon trend={h.trend?.trend ?? "flat"} />
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {geo.data && geo.data.cash_out_points.length > 0 && (
                <>
                  <h3 className="pt-1 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-subtle">Cash-out points</h3>
                  <ul className="space-y-1.5">
                    {geo.data.cash_out_points.map((c) => (
                      <li key={c.city} className="flex items-center justify-between rounded-lg border border-danger/30 bg-danger-soft px-2 py-1.5">
                        <span className="font-medium">{c.city}</span>
                        <span className="mono tnum">{c.count}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </Panel>
        </div>
        <div className="grid min-w-0 grid-cols-1 gap-3 xl:col-span-5 xl:grid-rows-[1fr_auto]">
          <div className="min-h-[300px]">
            <IncidentFeed incidents={run.incidents} banks={run.banks} height={300} />
          </div>
          <div className="min-h-[200px]">
            <AntibodyTicker antibodies={run.antibodies} banks={run.banks} otherBank={otherBank} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-12">
        <div className="min-w-0 xl:col-span-8">
          <RingPanel refreshKey={ringKey} />
        </div>
        <div className="min-w-0 xl:col-span-4">
          <BenchmarkMini />
        </div>
      </div>
    </div>
  );
}
