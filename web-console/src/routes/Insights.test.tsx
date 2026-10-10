import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderApp } from "@/test-utils";
import { fixtures } from "@/mocks/fixture";

vi.mock("@/lib/api/endpoints", () => ({ endpoints: { benchmark: vi.fn() } }));
import { endpoints } from "@/lib/api/endpoints";
import Insights from "./Insights";
import { formatCi, formatPct } from "@/lib/format";

const b = fixtures.benchmark;
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(endpoints.benchmark).mockResolvedValue(b);
});

describe("Insights page reads everything from the JSON", () => {
  it("headline gauges show the JSON values and intervals", async () => {
    renderApp(<Insights />);
    const prec = await screen.findByTestId("gauge-precision");
    expect(prec).toHaveTextContent(formatPct(b.headline.precision.value, 1));
    expect(prec).toHaveTextContent(formatCi(b.headline.precision.ci95, 1));
    expect(screen.getByTestId("gauge-recall")).toHaveTextContent(formatPct(b.headline.recall.value, 1));
    expect(screen.getByTestId("gauge-held-benign-rate")).toHaveTextContent(formatPct(b.headline.fpr.value, 2));
    expect(screen.getByTestId("gauge-flagged-benign-rate")).toHaveTextContent(formatPct(b.headline.flagged_fpr.value, 2));
  });

  it("NEFT callout sits with the headline and quotes the worst-rail figure", async () => {
    renderApp(<Insights />);
    const c = await screen.findByTestId("neft-callout");
    expect(c).toHaveTextContent("NEFT");
    expect(c).toHaveTextContent(formatPct(b.per_rail.NEFT!.flagged_fpr.value, 2));
    expect(c).toHaveTextContent(/no NEFT scams/);
  });

  it("per-rail table has a row per rail with counts from the JSON", async () => {
    renderApp(<Insights />);
    await screen.findByTestId("rail-table");
    for (const [rail, h] of Object.entries(b.per_rail)) {
      const row = screen.getByTestId(`rail-${rail}`);
      expect(row).toHaveTextContent(h.n_benign.toLocaleString("en-IN"));
      expect(row).toHaveTextContent(formatPct(h.flagged_fpr.value, 2));
    }
    expect(within(screen.getByTestId("rail-NEFT")).getByText("worst")).toBeInTheDocument();
  });

  it("ablation table, hero panel, prevalence table and meta all come from the file", async () => {
    renderApp(<Insights />);
    const ab = await screen.findByTestId("ablation-table");
    expect(ab).toHaveTextContent(formatPct(b.ablations.no_antibody!.victim_transfer_recall!.value, 1));
    const hero = screen.getByTestId("hero-panel");
    expect(hero).toHaveTextContent(`${b.hero!.model_only.allow} allowed`);
    expect(hero).toHaveTextContent(`${b.hero!.with_antibody.hold_verify} held`);
    expect(screen.getByTestId("prevalence-table")).toHaveTextContent("this benchmark");
    expect(screen.getByTestId("meta")).toHaveTextContent(b.meta.git_commit);
    expect(screen.getByTestId("delay-takeaway")).toHaveTextContent(/stops helping at 24 h/);
  });

  it("limitations are always rendered in full", async () => {
    renderApp(<Insights />);
    const lim = await screen.findByTestId("limitations");
    for (const l of b.limitations) expect(lim).toHaveTextContent(l.slice(0, 40));
  });

  it("shows an error state with retry", async () => {
    vi.mocked(endpoints.benchmark).mockRejectedValue(new Error("x"));
    renderApp(<Insights />);
    expect(await screen.findByText("Benchmark unavailable")).toBeInTheDocument();
  });
});
