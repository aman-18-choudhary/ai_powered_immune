import type { BenchAblation, BenchHeadline, BenchSensitivity, BenchmarkSummary, Metric } from "@/types/api";

export interface Scale {
  (v: number): number;
  domain: [number, number];
  range: [number, number];
}

export function linearScale(domain: [number, number], range: [number, number]): Scale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0;
  const f = ((v: number) => (span === 0 ? r0 : r0 + ((v - d0) / span) * (r1 - r0))) as Scale;
  f.domain = domain;
  f.range = range;
  return f;
}

/** Evenly spaced tick values covering [0, max] with a "nice" step (1, 2, 2.5 or 5 times 10^k). */
export function niceTicks(max: number, target = 5): number[] {
  if (!(max > 0)) return [0];
  const raw = max / target;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const frac = raw / pow;
  const step = (frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 2.5 ? 2.5 : frac <= 5 ? 5 : 10) * pow;
  const out: number[] = [];
  for (let i = 0; i < 50; i++) {
    const v = +(i * step).toFixed(10);
    out.push(v);
    if (v >= max - 1e-12) break;
  }
  return out;
}

/** Upper axis bound for a set of rates: next nice value above the max, never below `min`. */
export function axisMax(values: (number | null | undefined)[], min = 0.01): number {
  const m = Math.max(min, ...values.map((v) => (typeof v === "number" ? v : 0)));
  const t = niceTicks(m * 1.1, 4);
  return t[t.length - 1] ?? m;
}

/** Positive predictive value at a given prevalence from recall and FPR. Returns null when undefined. */
export function ppv(recall: number | null, fpr: number | null, prevalence: number): number | null {
  if (recall === null || fpr === null || !(prevalence > 0) || prevalence >= 1) return null;
  const tp = recall * prevalence;
  const fp = fpr * (1 - prevalence);
  return tp + fp === 0 ? null : tp / (tp + fp);
}

export interface PpvRow {
  label: string;
  prevalence: number;
  point: number | null;
  /** Using the upper bound of the FPR CI: the conservative figure. */
  conservative: number | null;
  isBenchmark: boolean;
}

export function prevalenceTable(h: BenchHeadline, extra: number[] = [0.01, 0.001, 0.0001]): PpvRow[] {
  const bench = h.n_scam / Math.max(1, h.n_scam + h.n_benign);
  const fprHi = h.fpr.ci95 ? h.fpr.ci95[1] : h.fpr.value;
  const rows: PpvRow[] = [
    { label: "Benchmark mix", prevalence: bench, isBenchmark: true, point: null, conservative: null },
    ...extra.map((p) => ({ label: `${(p * 100).toString()}% scam`, prevalence: p, isBenchmark: false, point: null, conservative: null })),
  ];
  return rows.map((r) => ({
    ...r,
    point: ppv(h.recall.value, h.fpr.value, r.prevalence),
    conservative: ppv(h.recall.value, fprHi, r.prevalence),
  }));
}

export function worstRail(per: Record<string, BenchHeadline>): { rail: string; flagged: Metric; held: Metric; nBenign: number; nScam: number } | null {
  let best: { rail: string; flagged: Metric; held: Metric; nBenign: number; nScam: number } | null = null;
  for (const [rail, h] of Object.entries(per)) {
    const v = h.flagged_fpr.value;
    if (v === null) continue;
    if (!best || v > (best.flagged.value ?? -1)) best = { rail, flagged: h.flagged_fpr, held: h.fpr, nBenign: h.n_benign, nScam: h.n_scam };
  }
  return best;
}

export function formatDelay(s: number | null): string {
  if (s === null) return "none";
  if (s === 0) return "0 s";
  if (s < 120) return `${s} s`;
  if (s < 7200) return `${Math.round(s / 60)} min`;
  return `${+(s / 3600).toFixed(1)} h`;
}

export interface DelayPoint {
  delay: number;
  recall: number;
  lo: number | null;
  hi: number | null;
  label: string;
  held: number | null;
  matches: number | null;
}

/** Label-gated sensitivity rows ordered by delay, as chart points. */
export function delayPoints(rows: readonly BenchSensitivity[]): DelayPoint[] {
  return rows
    .filter((r) => r.gate === "label" && r.delay_s !== null && r.victim_transfer_recall.value !== null)
    .map((r) => ({
      delay: r.delay_s as number,
      recall: r.victim_transfer_recall.value as number,
      lo: r.victim_transfer_recall.ci95?.[0] ?? null,
      hi: r.victim_transfer_recall.ci95?.[1] ?? null,
      label: r.label,
      held: r.mean_share_post_detection_held.value,
      matches: r.antibody_matches ?? null,
    }))
    .sort((a, b) => a.delay - b.delay);
}

export function noAntibodyRecall(rows: readonly BenchSensitivity[]): number | null {
  const r = rows.find((x) => x.gate === "none" && x.delay_s === null);
  return r?.victim_transfer_recall.value ?? null;
}

/**
 * The first delay at which the antibody no longer helps: victim-transfer recall is no better than
 * with no antibody at all. Null when it helps at every tested delay.
 */
export function stopsHelpingAt(points: readonly DelayPoint[], noAntibody: number | null, eps = 1e-6): DelayPoint | null {
  if (noAntibody === null) return null;
  return points.find((p) => p.recall <= noAntibody + eps) ?? null;
}

export interface AblationBar {
  variant: string;
  label: string;
  value: number | null;
  ci: [number, number] | null;
}

export const VARIANT_LABEL: Record<string, string> = {
  baseline: "Antibody on (baseline)",
  no_antibody: "No antibody",
  no_call_signal: "No call signal",
  degraded_call_signal: "Degraded call signal",
};
export const VARIANT_ORDER = ["baseline", "no_antibody", "no_call_signal", "degraded_call_signal"];

export type AblationMetric = "victim_transfer_recall" | "recall" | "mule_forward_recall";

export function ablationBars(ab: Record<string, BenchAblation>, metric: AblationMetric): AblationBar[] {
  const names = [...VARIANT_ORDER.filter((v) => v in ab), ...Object.keys(ab).filter((v) => !VARIANT_ORDER.includes(v))];
  return names.map((variant) => {
    const m = ab[variant]?.[metric];
    return { variant, label: VARIANT_LABEL[variant] ?? variant.replace(/_/g, " "), value: m?.value ?? null, ci: m?.ci95 ?? null };
  });
}

/** Everything the headline gauges need, read from the JSON. */
export function headlineGauges(s: BenchmarkSummary) {
  const h = s.headline;
  return [
    { key: "precision", label: "Precision", m: h.precision, good: "high" as const, note: "Of transactions held, the share that were scams." },
    { key: "recall", label: "Recall", m: h.recall, good: "high" as const, note: "Of scam transactions, the share held or stepped up." },
    { key: "fpr", label: "Held-benign rate", m: h.fpr, good: "low" as const, note: "Benign transactions that were held (FPR)." },
    { key: "flagged_fpr", label: "Flagged-benign rate", m: h.flagged_fpr, good: "low" as const, note: "Benign transactions that got a step-up or a hold." },
  ];
}
