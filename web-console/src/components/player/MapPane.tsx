import { Suspense, lazy, useMemo } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight, Map as MapIcon } from "lucide-react";
import { Panel, Skeleton } from "@/components/common/Panel";
import type { RunState } from "@/state/reducer";
import { LedgerChain } from "./LedgerChain";

const IndiaMap = lazy(() => import("@/components/common/IndiaMap"));

export function MapPane({ run }: { run: RunState }) {
  const points = useMemo(() => run.geo.map((g) => ({ lat: g.lat, lon: g.lon, kind: g.kind, city: g.city, state: g.state, t: g.t })), [run.geo]);
  const ring = Object.values(run.rings).sort((a, b) => b.t - a.t)[0];
  return (
    <Panel
      title="Command centre · live map"
      icon={<MapIcon className="h-3.5 w-3.5" aria-hidden />}
      className="h-full"
      actions={
        <Link to="/command" className="inline-flex items-center gap-1 text-[11px] font-medium text-flow hover:underline">
          Open <ArrowUpRight className="h-3 w-3" aria-hidden />
        </Link>
      }
      bodyClassName="flex min-h-0 flex-col gap-2 p-3"
      aria-label="Command centre mini map"
    >
      <div className="relative min-h-0 flex-1">
        <Suspense fallback={<Skeleton className="h-full w-full" />}>
          <IndiaMap points={points} compact className="h-full" />
        </Suspense>
      </div>
      {ring && (
        <div className="flex items-center justify-between rounded-lg border border-line bg-chip px-2.5 py-1.5 text-[11.5px]" data-testid="ring-summary">
          <span className="font-medium">Ring forming</span>
          <span className="mono tnum text-muted">
            {ring.members} {ring.members === 1 ? "account" : "accounts"} · {ring.victims} {ring.victims === 1 ? "victim" : "victims"}
          </span>
        </div>
      )}
      <div>
        <h3 className="mb-1 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-subtle">Evidence ledger</h3>
        <LedgerChain entries={run.ledger} />
      </div>
    </Panel>
  );
}
