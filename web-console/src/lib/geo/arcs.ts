import type { PointKind } from "@/types/api";

export interface GeoPointLite {
  lat: number;
  lon: number;
  kind: PointKind;
  city: string;
  state: string;
  t: number;
}

export interface CityAgg {
  city: string;
  state: string;
  lat: number;
  lon: number;
  kinds: Partial<Record<PointKind, number>>;
  total: number;
  lastT: number;
}

export function aggregateCities(points: GeoPointLite[]): CityAgg[] {
  const m = new Map<string, CityAgg>();
  for (const p of points) {
    const k = `${p.city}|${p.state}`;
    let a = m.get(k);
    if (!a) {
      a = { city: p.city, state: p.state, lat: p.lat, lon: p.lon, kinds: {}, total: 0, lastT: p.t };
      m.set(k, a);
    }
    a.kinds[p.kind] = (a.kinds[p.kind] ?? 0) + 1;
    a.total += 1;
    a.lastT = Math.max(a.lastT, p.t);
  }
  return [...m.values()].sort((a, b) => a.city.localeCompare(b.city));
}

export interface ArcSpec {
  id: string;
  from: CityAgg;
  to: CityAgg;
}

/**
 * Payment paths: every victim city (scam call / held transfer / protected) to every cash-out
 * city seen so far. The mule layer has no geography, so the arc honestly goes victim -> cash-out.
 */
export function buildArcs(cities: CityAgg[]): ArcSpec[] {
  const victims = cities.filter((c) => (c.kinds.held_transfer ?? 0) + (c.kinds.protected ?? 0) > 0);
  const cashOuts = cities.filter((c) => (c.kinds.cash_out ?? 0) > 0);
  const out: ArcSpec[] = [];
  for (const v of victims) {
    for (const c of cashOuts) {
      if (v.city === c.city) continue;
      out.push({ id: `${v.city}>${c.city}`, from: v, to: c });
    }
  }
  return out;
}

/** Quadratic bezier path with the control point lifted perpendicular to the chord. */
export function arcPath(a: [number, number], b: [number, number], lift = 0.28): string {
  const mx = (a[0] + b[0]) / 2;
  const my = (a[1] + b[1]) / 2;
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const len = Math.hypot(dx, dy) || 1;
  // perpendicular, always bowing "up" on screen
  let nx = -dy / len;
  let ny = dx / len;
  if (ny > 0) {
    nx = -nx;
    ny = -ny;
  }
  const cx = mx + nx * len * lift;
  const cy = my + ny * len * lift;
  return `M${a[0].toFixed(1)},${a[1].toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${b[0].toFixed(1)},${b[1].toFixed(1)}`;
}

export function dominantKind(kinds: Partial<Record<PointKind, number>>): PointKind {
  const order: PointKind[] = ["cash_out", "scam_call", "held_transfer", "protected"];
  let best: PointKind = "held_transfer";
  let n = -1;
  for (const k of order) {
    const v = kinds[k] ?? 0;
    if (v > n) {
      best = k;
      n = v;
    }
  }
  return best;
}
