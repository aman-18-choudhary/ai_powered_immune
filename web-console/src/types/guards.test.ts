import { describe, expect, it } from "vitest";
import { parseEvent, parseCounters } from "./guards";
import { rawHeroRun } from "@/mocks/fixture";

describe("parseEvent", () => {
  it("accepts every recorded server event", () => {
    for (const r of rawHeroRun) expect(parseEvent(r)).not.toBeNull();
  });
  it("ignores unknown kinds and malformed envelopes", () => {
    expect(parseEvent({ id: 1, t: 0, kind: "future.thing" })).toBeNull();
    expect(parseEvent({ id: "1", t: 0, kind: "call.alert" })).toBeNull();
    expect(parseEvent(null)).toBeNull();
    expect(parseEvent([])).toBeNull();
    expect(parseEvent({ id: 1, t: 0, kind: "call.chunk" })).toBeNull(); // missing call_ref
  });
  it("reads the colliding fields under their real names", () => {
    const g = parseEvent({ id: 3, t: 1, kind: "geo.point", lat: 1, lon: 2, point_kind: "cash_out", city: "X", state: "Y" });
    expect(g?.kind === "geo.point" && g.point_kind).toBe("cash_out");
    const a = parseEvent({ id: 4, t: 1, kind: "antibody.published", antibody_ref: "r", antibody_kind: "mule_account", bank: "bank_a" });
    expect(a?.kind === "antibody.published" && a.antibody_kind).toBe("mule_account");
    expect(parseEvent({ id: 3, t: 1, kind: "geo.point", lat: 1, lon: 2, point_kind: "weird", city: "", state: "" })).toBeNull();
  });
  it("does not nest snapshots", () => {
    const nested = { id: 0, t: 0, kind: "scenario.state", events: [{ id: 1, t: 0, kind: "scenario.state", events: [] }] };
    const p = parseEvent(nested);
    expect(p?.kind === "scenario.state" && p.events.length).toBe(0);
  });
  it("coerces missing counters to zero", () => {
    expect(parseCounters({ chunks: 3 }).chunks).toBe(3);
    expect(parseCounters(null).holds_open).toBe(0);
  });
});
