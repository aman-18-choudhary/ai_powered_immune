import { describe, expect, it } from "vitest";
import { applyEvents, initialRun } from "@/state/reducer";
import { fixtures, heroEvents } from "@/mocks/fixture";
import { buildHoldTimeline } from "./timeline";

describe("hold timeline from the event store", () => {
  const run = applyEvents(initialRun(), heroEvents());
  const view = fixtures.holds.holds.find((h) => h.state === "blocked")!;
  const items = buildHoldTimeline(run, view);

  it("starts with the call alert and ends with the resolution, in time order", () => {
    expect(items[0]!.kind).toBe("call");
    expect(items[items.length - 1]!.kind).toBe("resolved");
    for (let i = 1; i < items.length; i++) expect(items[i]!.t).toBeGreaterThanOrEqual(items[i - 1]!.t);
  });
  it("includes the decision, the antibody and ledger entries sealed while open", () => {
    const kinds = new Set(items.map((i) => i.kind));
    expect(kinds).toContain("decision");
    expect(kinds).toContain("antibody");
    expect(kinds).toContain("ledger");
  });
  it("a hold with no history still shows its current decision", () => {
    const empty = buildHoldTimeline(initialRun(), view);
    expect(empty.map((i) => i.kind)).toEqual(["decision"]);
  });
});
