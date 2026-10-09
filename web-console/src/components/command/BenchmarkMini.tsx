import { Link } from "react-router-dom";
import { ArrowUpRight, Gauge } from "lucide-react";
import { Panel, EmptyState, Skeleton } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { endpoints } from "@/lib/api/endpoints";
import { useRest } from "@/lib/hooks/useRest";
import { formatCi, formatPct } from "@/lib/format";
import type { BenchmarkSummary, Metric } from "@/types/api";

function worstRail(b: BenchmarkSummary): { rail: string; metric: Metric } | null {
  let best: { rail: string; metric: Metric } | null = null;
  for (const [rail, h] of Object.entries(b.per_rail)) {
    const m = h.flagged_fpr;
    if (m.value === null) continue;
    if (!best || m.value > (best.metric.value ?? -1)) best = { rail, metric: m };
  }
  return best;
}

function Stat({ label, m, digits = 1, hint }: { label: string; m: Metric; digits?: number; hint?: string }) {
  return (
    <div className="rounded-xl border border-line bg-chip px-3 py-2">
      <div className="text-[10.5px] font-semibold uppercase tracking-wider text-subtle">{label}</div>
      <div className="mono tnum mt-0.5 text-xl font-semibold">{formatPct(m.value, digits)}</div>
      <div className="mono text-[10.5px] text-subtle">{m.ci95 ? `95% CI ${formatCi(m.ci95, digits)}` : hint ?? "no CI"}</div>
    </div>
  );
}

export function BenchmarkMini() {
  const { data, loading, error, reload } = useRest<BenchmarkSummary>((s) => endpoints.benchmark(s), 0);
  const worst = data ? worstRail(data) : null;
  return (
    <Panel
      title="Benchmark (simulator only)"
      icon={<Gauge className="h-3.5 w-3.5" aria-hidden />}
      className="h-full"
      actions={
        <Link to="/insights" className="inline-flex items-center gap-1 text-[11px] font-medium text-flow hover:underline">
          Insights <ArrowUpRight className="h-3 w-3" aria-hidden />
        </Link>
      }
      bodyClassName="space-y-3 p-3"
    >
      {!data && loading && (
        <>
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </>
      )}
      {!data && error && (
        <EmptyState title={error}>
          <Button size="sm" variant="secondary" onClick={reload}>Retry</Button>
        </EmptyState>
      )}
      {data && (
        <>
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Precision" m={data.headline.precision} />
            <Stat label="Recall" m={data.headline.recall} />
            <Stat label="FPR" m={data.headline.fpr} digits={2} />
          </div>
          {worst && (
            <div className="rounded-xl border border-warn/40 bg-warn-soft px-3 py-2 text-xs" data-testid="worst-rail">
              <span className="font-semibold">Worst rail for flagged FPR: {worst.rail}</span>{" "}
              <span className="mono tnum">
                {formatPct(worst.metric.value, 2)} ({formatCi(worst.metric.ci95, 2)})
              </span>
              <span className="text-muted"> of benign transactions get a step-up or hold.</span>
            </div>
          )}
          <p className="text-[11px] leading-snug text-subtle">
            {data.headline.n_scam.toLocaleString("en-IN")} scam and {data.headline.n_benign.toLocaleString("en-IN")} benign synthetic
            transactions, seed {data.meta.seed}, commit <span className="mono">{data.meta.git_commit}</span>. Synthetic data from the same
            generators the models were trained on; not real-world performance.
          </p>
        </>
      )}
    </Panel>
  );
}
