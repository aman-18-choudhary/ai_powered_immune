import { useMemo } from "react";
import { AlertTriangle, BarChart3, FlaskConical, Info, LineChart, Scale, Target } from "lucide-react";
import { EmptyState, Panel, Skeleton } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { DecisionBar, GroupedBars, RangeGauge, axisMax, type BarGroup } from "@/components/insights/charts";
import { useRest } from "@/lib/hooks/useRest";
import { usePageTitle } from "@/lib/hooks/usePageTitle";
import { endpoints } from "@/lib/api/endpoints";
import { formatCi, formatPct } from "@/lib/format";
import { DelayChart } from "@/components/insights/charts";
import { ablationBars, headlineGauges, prevalenceTable, worstRail } from "@/lib/insights/math";
import type { BenchmarkSummary, Metric } from "@/types/api";

const n = (v: number) => v.toLocaleString("en-IN");

function MetricCell({ m, d = 2 }: { m: Metric; d?: number }) {
  return (
    <span className="mono tnum">
      {formatPct(m.value, d)} <span className="text-subtle">{m.ci95 ? `(${formatCi(m.ci95, d)})` : ""}</span>
    </span>
  );
}

function Limitations({ items }: { items: string[] }) {
  return (
    <Panel
      title="Limitations"
      icon={<AlertTriangle className="h-3.5 w-3.5 text-warn" aria-hidden />}
      className="border-warn/40"
      bodyClassName="max-h-[70dvh] overflow-y-auto p-4"
      data-testid="limitations"
    >
      <p className="mb-3 text-xs font-semibold text-warn">Read these before quoting any number on this page.</p>
      <ol className="space-y-3 text-[12.5px] leading-snug text-muted">
        {items.map((l, i) => (
          <li key={i} className="flex gap-2">
            <span className="mono mt-0.5 shrink-0 text-subtle">{i + 1}.</span>
            <span>{l}</span>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

function Body({ b }: { b: BenchmarkSummary }) {
  const gauges = headlineGauges(b);
  const worst = worstRail(b.per_rail);
  const rails = Object.entries(b.per_rail);
  const fprMax = axisMax(rails.flatMap(([, h]) => [h.flagged_fpr.ci95?.[1], h.flagged_fpr.value, h.fpr.ci95?.[1]]), 0.005);
  const groups: BarGroup[] = [
    { key: "victim", label: "Victim transfers", bars: ablationBars(b.ablations, "victim_transfer_recall") },
    { key: "mule", label: "Mule forwards", bars: ablationBars(b.ablations, "mule_forward_recall") },
    { key: "all", label: "All scam transactions", bars: ablationBars(b.ablations, "recall") },
  ];
  const prev = useMemo(() => prevalenceTable(b.headline), [b.headline]);
  const roles = Object.entries(b.per_role ?? {});
  const hero = b.hero;
  const heroTotal = hero ? hero.seeds : 0;
  const rate = (m: Metric) => axisMax([m.ci95?.[1], m.value], 0.01);

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
      <div className="min-w-0 space-y-4">
        {/* headline */}
        <Panel title="Headline (simulator only)" icon={<Target className="h-3.5 w-3.5" aria-hidden />} bodyClassName="space-y-3 p-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {gauges.map((g) => (
              <RangeGauge
                key={g.key}
                label={g.label}
                note={g.note}
                metric={g.m}
                digits={g.good === "low" ? 2 : 1}
                max={g.good === "low" ? rate(g.m) : 1}
                tone={g.good === "low" ? "var(--warn)" : "var(--intel)"}
              />
            ))}
          </div>
          {worst && (
            <div className="rounded-xl border border-warn/40 bg-warn-soft px-3 py-2.5 text-sm" data-testid="neft-callout">
              <strong>Worst-rail figure: {worst.rail}.</strong>{" "}
              <span className="mono tnum">
                {formatPct(worst.flagged.value, 2)} {worst.flagged.ci95 ? `(95% CI ${formatCi(worst.flagged.ci95, 2)})` : ""}
              </span>{" "}
              of {n(worst.nBenign)} benign {worst.rail} transactions get a step-up or a hold, against {formatPct(b.headline.flagged_fpr.value, 2)} overall.
              {worst.nScam === 0 && (
                <span className="text-muted">
                  {" "}
                  The simulator has no {worst.rail} scams, so {worst.rail} recall is undefined here, not good.
                </span>
              )}
            </div>
          )}
          <p className="text-xs text-subtle">
            {n(b.headline.n_scam)} scam and {n(b.headline.n_benign)} benign synthetic transactions
            {b.config ? ` (${b.config.n_citizens ? n(b.config.n_citizens) : "?"} citizens, ${b.config.days ?? "?"} days, ${b.config.n_campaigns ?? "?"} campaigns)` : ""}.
            Intervals are Wilson 95%.
          </p>
        </Panel>

        {/* per rail */}
        <Panel title="False positives by rail" icon={<BarChart3 className="h-3.5 w-3.5" aria-hidden />} bodyClassName="overflow-x-auto p-0">
          <table className="w-full min-w-[640px] text-left text-sm" data-testid="rail-table">
            <caption className="sr-only">Per-rail benign counts, held-benign and flagged-benign rates and recall with 95% intervals</caption>
            <thead className="text-[11px] uppercase tracking-wider text-subtle">
              <tr className="border-b border-line">
                <th scope="col" className="px-4 py-2 font-semibold">Rail</th>
                <th scope="col" className="px-2 py-2 text-right font-semibold">Benign</th>
                <th scope="col" className="px-2 py-2 text-right font-semibold">Scam</th>
                <th scope="col" className="px-2 py-2 font-semibold">Held-benign</th>
                <th scope="col" className="px-2 py-2 font-semibold">Flagged-benign</th>
                <th scope="col" className="px-4 py-2 font-semibold">Recall</th>
              </tr>
            </thead>
            <tbody>
              {rails.map(([rail, h]) => {
                const isWorst = worst?.rail === rail;
                const w = (h.flagged_fpr.value ?? 0) / fprMax;
                return (
                  <tr key={rail} className="border-b border-line last:border-0" data-testid={`rail-${rail}`}>
                    <th scope="row" className="px-4 py-2.5 font-semibold">
                      {rail}
                      {isWorst && <span className="ml-2 rounded border border-warn/40 bg-warn-soft px-1 py-0.5 text-[10px] font-semibold text-warn">worst</span>}
                    </th>
                    <td className="mono tnum px-2 py-2.5 text-right">{n(h.n_benign)}</td>
                    <td className="mono tnum px-2 py-2.5 text-right">{n(h.n_scam)}</td>
                    <td className="px-2 py-2.5">
                      <MetricCell m={h.fpr} />
                    </td>
                    <td className="px-2 py-2.5">
                      <MetricCell m={h.flagged_fpr} />
                      <div className="mt-1 h-1 w-32 overflow-hidden rounded-full bg-raised" aria-hidden>
                        <div className="h-full rounded-full bg-warn" style={{ width: `${Math.min(100, w * 100)}%` }} />
                      </div>
                    </td>
                    <td className="px-4 py-2.5">{h.n_scam === 0 || h.recall.value === null ? <span className="text-muted">n/a (no scams)</span> : <MetricCell m={h.recall} d={1} />}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Panel>

        {/* per role */}
        {roles.length > 0 && (
          <Panel title="Recall by role in the scam" icon={<Target className="h-3.5 w-3.5" aria-hidden />} bodyClassName="grid gap-3 p-4 sm:grid-cols-2">
            {roles.map(([role, r]) => (
              <RangeGauge
                key={role}
                label={role === "victim_transfer" ? "Victim transfers" : role === "mule_forward" ? "Mule forwards" : role.replace(/_/g, " ")}
                note={`${n(r.held)} held and ${n(r.flagged)} flagged of ${n(r.n)} transactions.`}
                metric={r.recall}
                tone={role === "victim_transfer" ? "var(--safe)" : "var(--warn)"}
              />
            ))}
          </Panel>
        )}

        {/* ablations */}
        <Panel title="Ablations: what each signal contributes" icon={<FlaskConical className="h-3.5 w-3.5" aria-hidden />} bodyClassName="space-y-3 p-4">
          <GroupedBars groups={groups} />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-xs" data-testid="ablation-table">
              <caption className="sr-only">Ablation variants with recall, precision and flagged-benign rate</caption>
              <thead className="text-[10.5px] uppercase tracking-wider text-subtle">
                <tr className="border-b border-line">
                  <th scope="col" className="py-1.5 pr-2 font-semibold">Variant</th>
                  <th scope="col" className="px-2 py-1.5 font-semibold">Victim-transfer recall</th>
                  <th scope="col" className="px-2 py-1.5 font-semibold">Overall recall</th>
                  <th scope="col" className="px-2 py-1.5 font-semibold">Precision</th>
                  <th scope="col" className="px-2 py-1.5 font-semibold">Flagged-benign</th>
                </tr>
              </thead>
              <tbody>
                {groups[0]?.bars.map((bar) => {
                  const a = b.ablations[bar.variant];
                  if (!a) return null;
                  return (
                    <tr key={bar.variant} className="border-b border-line last:border-0">
                      <th scope="row" className="py-2 pr-2 font-medium">{bar.label}</th>
                      <td className="px-2 py-2">{a.victim_transfer_recall ? <MetricCell m={a.victim_transfer_recall} d={1} /> : "n/a"}</td>
                      <td className="px-2 py-2"><MetricCell m={a.recall} d={1} /></td>
                      <td className="px-2 py-2"><MetricCell m={a.precision} d={1} /></td>
                      <td className="px-2 py-2"><MetricCell m={a.flagged_fpr} d={2} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>

        {/* sensitivity */}
        <Panel title="How late can the confirmation be?" icon={<LineChart className="h-3.5 w-3.5" aria-hidden />} bodyClassName="space-y-2 p-4">
          <p className="text-xs text-muted">
            The antibody is published when an analyst confirms a mule. This sweeps that delay
            {b.antibody?.confirm_delay_s !== undefined ? ` (the baseline assumes ${b.antibody.confirm_delay_s} s)` : ""} from immediate to a full day.
          </p>
          <DelayChart rows={b.sensitivity} />
        </Panel>

        {/* hero */}
        {hero && (
          <Panel title="Hero hard case across seeds" icon={<Target className="h-3.5 w-3.5" aria-hidden />} bodyClassName="space-y-4 p-4" data-testid="hero-panel">
            <p className="text-xs text-muted">
              Victim B&apos;s first transfer to a seasoned mule ({hero.mule_age_days_range ? `${hero.mule_age_days_range[0]} to ${hero.mule_age_days_range[1]} days old` : "old account"}) when call-guard missed the second call, over {hero.seeds} seeds.
            </p>
            <DecisionBar title="Model only (no antibody)" counts={hero.model_only} total={heroTotal} />
            <DecisionBar title="With the shared antibody" counts={hero.with_antibody} total={heroTotal} />
            <p className="text-xs text-subtle">
              A constructed hard case: it shows what shared threat memory adds when the model alone fails, not how often this happens.
            </p>
          </Panel>
        )}

        {/* prevalence */}
        <Panel title="Precision at lower scam prevalence" icon={<Scale className="h-3.5 w-3.5" aria-hidden />} bodyClassName="space-y-2 p-4">
          <p className="text-xs text-muted">
            Precision depends on how rare scams are; recall and FPR do not. Computed here in the browser from this file&apos;s headline recall and held-benign rate (the file does not contain it).
            The conservative column uses the upper end of the FPR interval.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[420px] text-left text-sm" data-testid="prevalence-table">
              <caption className="sr-only">Precision at different scam prevalences</caption>
              <thead className="text-[11px] uppercase tracking-wider text-subtle">
                <tr className="border-b border-line">
                  <th scope="col" className="py-1.5 pr-2 font-semibold">Scam prevalence</th>
                  <th scope="col" className="px-2 py-1.5 font-semibold">Precision</th>
                  <th scope="col" className="px-2 py-1.5 font-semibold">Conservative</th>
                </tr>
              </thead>
              <tbody>
                {prev.map((r) => (
                  <tr key={r.label} className="border-b border-line last:border-0">
                    <th scope="row" className="py-2 pr-2 font-medium">
                      {r.isBenchmark ? `${(r.prevalence * 100).toFixed(2)}% (this benchmark)` : r.label}
                    </th>
                    <td className="mono tnum px-2 py-2">{formatPct(r.point, 1)}</td>
                    <td className="mono tnum px-2 py-2">{formatPct(r.conservative, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        {/* meta */}
        <Panel title="Reproducibility" icon={<Info className="h-3.5 w-3.5" aria-hidden />} bodyClassName="p-4">
          <dl className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-1.5 text-xs" data-testid="meta">
            <dt className="text-muted">Seed</dt>
            <dd className="mono">{b.meta.seed}</dd>
            <dt className="text-muted">Git commit</dt>
            <dd className="mono">{b.meta.git_commit}</dd>
            {b.meta.runtime_s != null && (
              <>
                <dt className="text-muted">Runtime</dt>
                <dd className="mono">{b.meta.runtime_s.toFixed(0)} s</dd>
              </>
            )}
            {b.meta.model_versions && (
              <>
                <dt className="text-muted">Models</dt>
                <dd className="mono">{Object.entries(b.meta.model_versions).map(([k, v]) => `${k} ${v}`).join(" · ")}</dd>
              </>
            )}
            {b.latency_ms && (
              <>
                <dt className="text-muted">Scoring latency</dt>
                <dd className="mono">
                  p50 {b.latency_ms.p50.toFixed(1)} ms · p99 {b.latency_ms.p99.toFixed(1)} ms (n={n(b.latency_ms.n)})
                </dd>
              </>
            )}
            {b.meta.command && (
              <>
                <dt className="text-muted">Command</dt>
                <dd className="mono break-all">{b.meta.command}</dd>
              </>
            )}
          </dl>
        </Panel>
      </div>

      <aside className="min-w-0 xl:sticky xl:top-[72px] xl:self-start" aria-label="Limitations">
        <Limitations items={b.limitations} />
      </aside>
    </div>
  );
}

export default function Insights() {
  usePageTitle("Insights");
  const { data, loading, error, reload } = useRest<BenchmarkSummary>((s) => endpoints.benchmark(s), 0);
  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-3 px-4 py-4 md:px-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Insights</h1>
        <p className="text-sm text-muted">
          Benchmark results with their limits in view. Simulated data only: these figures are not real-world performance.
        </p>
      </header>
      {!data && loading && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-busy="true" aria-label="Loading benchmark">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      )}
      {!data && error && (
        <Panel>
          <EmptyState title="Benchmark unavailable" hint={error}>
            <Button size="sm" variant="secondary" onClick={reload}>
              Retry
            </Button>
          </EmptyState>
        </Panel>
      )}
      {data && <Body b={data} />}
    </div>
  );
}
