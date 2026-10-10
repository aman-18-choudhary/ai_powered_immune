import { describe, expect, it } from "vitest";
import { layoutRing, edgePath } from "./ringLayout";
import { aggregateCities, arcPath, buildArcs, dominantKind } from "./arcs";
import { fixtures } from "@/mocks/fixture";

describe("ring tier layout", () => {
  const { nodes, edges } = fixtures.ring;
  it("places one column per tier, victims left to cash-out right", () => {
    const p = layoutRing(nodes, edges, 640, 300);
    expect(p).toHaveLength(nodes.length);
    const byRole = (r: string) => p.filter((x) => nodes.find((n) => n.id === x.id)?.role === r);
    const vx = byRole("victim")[0]?.x ?? 0;
    const mx = byRole("mule")[0]?.x ?? 0;
    const cx = byRole("cash_out")[0]?.x ?? 0;
    expect(vx).toBeLessThan(mx);
    expect(mx).toBeLessThan(cx);
    for (const q of p) {
      expect(q.x).toBeGreaterThanOrEqual(0);
      expect(q.x).toBeLessThanOrEqual(640);
      expect(q.y).toBeGreaterThanOrEqual(0);
      expect(q.y).toBeLessThanOrEqual(300);
    }
  });
  it("is deterministic and order independent", () => {
    const a = layoutRing(nodes, edges, 640, 300);
    const b = layoutRing([...nodes].reverse(), [...edges].reverse(), 640, 300);
    const key = (l: typeof a) => l.map((x) => `${x.id}:${x.x.toFixed(1)}:${x.y.toFixed(1)}`).sort();
    expect(key(b)).toEqual(key(a));
  });
  it("handles empty and single-node graphs and produces a path", () => {
    expect(layoutRing([], [], 100, 100)).toEqual([]);
    const one = layoutRing([{ id: "a", tier: 0 }], [], 100, 100);
    expect(one[0]).toMatchObject({ x: 50, y: 50 });
    expect(edgePath({ x: 0, y: 0 }, { x: 10, y: 10 })).toMatch(/^M0.0,0.0 C/);
  });
});

describe("map arcs", () => {
  const pts = [
    { lat: 23, lon: 72, kind: "scam_call" as const, city: "Ahmedabad", state: "Gujarat", t: 0 },
    { lat: 23, lon: 72, kind: "held_transfer" as const, city: "Ahmedabad", state: "Gujarat", t: 1 },
    { lat: 22, lon: 88, kind: "protected" as const, city: "Kolkata", state: "West Bengal", t: 2 },
    { lat: 12, lon: 77, kind: "cash_out" as const, city: "Bengaluru", state: "Karnataka", t: 3 },
  ];
  it("aggregates cities and links victim cities to cash-out cities only", () => {
    const cities = aggregateCities(pts);
    expect(cities.map((c) => c.city)).toEqual(["Ahmedabad", "Bengaluru", "Kolkata"]);
    const arcs = buildArcs(cities);
    expect(arcs.map((a) => a.id).sort()).toEqual(["Ahmedabad>Bengaluru", "Kolkata>Bengaluru"]);
    expect(buildArcs(aggregateCities(pts.slice(0, 3)))).toEqual([]);
  });
  it("picks a dominant kind and builds an SVG path", () => {
    expect(dominantKind({ held_transfer: 3, scam_call: 1 })).toBe("held_transfer");
    expect(arcPath([0, 100], [100, 100])).toMatch(/^M0.0,100.0 Q50.0,/);
  });
});
