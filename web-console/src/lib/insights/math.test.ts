import { describe, expect, it } from "vitest";
import { fixtures } from "@/mocks/fixture";
import {
  ablationBars, axisMax, delayPoints, formatDelay, headlineGauges, linearScale, niceTicks, noAntibodyRecall, ppv, prevalenceTable, stopsHelpingAt, worstRail,
} from "./math";

const b = fixtures.benchmark;

describe("scales and ticks", () => {
  it("maps domain to range and handles a degenerate domain", () => {
    const s = linearScale([0, 10], [100, 300]);
    expect(s(0)).toBe(100);
    expect(s(5)).toBe(200);
    expect(linearScale([2, 2], [0, 50])(2)).toBe(0);
  });
  it("produces nice ticks that cover the max", () => {
    const t = niceTicks(0.0083, 4);
    expect(t[0]).toBe(0);
    expect(t[t.length - 1]!).toBeGreaterThanOrEqual(0.0083);
    expect(niceTicks(1, 5)).toEqual([0, 0.2, 0.4, 0.6, 0.8, 1]);
    expect(axisMax([0.0083, 0.0002])).toBeGreaterThanOrEqual(0.0083);
  });
});

describe("prevalence-adjusted precision", () => {
  it("matches Bayes by hand", () => {
    // recall 0.8, fpr 0.01, prevalence 0.1 -> 0.08 / (0.08 + 0.009) = 0.898876...
    expect(ppv(0.8, 0.01, 0.1)).toBeCloseTo(0.8988764, 6);
    expect(ppv(null, 0.01, 0.1)).toBeNull();
    expect(ppv(0.8, 0, 0)).toBeNull();
  });
  it("falls as prevalence falls and the conservative figure is never above the point figure", () => {
    const rows = prevalenceTable(b.headline);
    expect(rows[0]!.isBenchmark).toBe(true);
    expect(rows[0]!.prevalence).toBeCloseTo(b.headline.n_scam / (b.headline.n_scam + b.headline.n_benign), 9);
    for (const r of rows) expect(r.conservative!).toBeLessThanOrEqual(r.point! + 1e-12);
    const conservative = rows.map((r) => r.conservative!);
    for (let i = 1; i < conservative.length; i++) expect(conservative[i]!).toBeLessThan(conservative[i - 1]!);
  });
});

describe("worst rail and headline", () => {
  it("picks the rail with the highest flagged FPR (NEFT in the recorded run)", () => {
    const w = worstRail(b.per_rail)!;
    expect(w.rail).toBe("NEFT");
    expect(w.flagged.value).toBeCloseTo(0.008278, 6);
    expect(w.nScam).toBe(0);
  });
  it("exposes the four headline gauges straight from the JSON", () => {
    const g = headlineGauges(b);
    expect(g.map((x) => x.key)).toEqual(["precision", "recall", "fpr", "flagged_fpr"]);
    expect(g[1]!.m.value).toBe(b.headline.recall.value);
  });
});

describe("delay sensitivity", () => {
  const pts = delayPoints(b.sensitivity);
  it("uses label-gated rows in delay order", () => {
    expect(pts.map((p) => p.delay)).toEqual([0, 60, 300, 900, 3600, 21600, 86400]);
  });
  it("finds where the antibody stops helping: at the delay where recall equals no-antibody recall", () => {
    const none = noAntibodyRecall(b.sensitivity);
    expect(none).toBeCloseTo(0.945355, 6);
    const stop = stopsHelpingAt(pts, none);
    expect(stop?.delay).toBe(86400);
    expect(stopsHelpingAt(pts.slice(0, 5), none)).toBeNull();
  });
  it("formats delays", () => {
    expect(formatDelay(0)).toBe("0 s");
    expect(formatDelay(300)).toBe("5 min");
    expect(formatDelay(21600)).toBe("6 h");
    expect(formatDelay(null)).toBe("none");
  });
});

describe("ablation bars", () => {
  it("lists variants in a stable order with values and CIs from the JSON", () => {
    const bars = ablationBars(b.ablations, "victim_transfer_recall");
    expect(bars.map((x) => x.variant)).toEqual(["baseline", "no_antibody", "no_call_signal", "degraded_call_signal"]);
    expect(bars[1]!.value).toBeCloseTo(0.945355, 6);
    expect(bars[0]!.ci).toEqual(b.ablations.baseline!.victim_transfer_recall!.ci95);
  });
});
