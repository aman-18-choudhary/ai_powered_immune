import type { GraphEdge, GraphNode } from "./network";

export interface Pos {
  x: number;
  y: number;
}

const TAU = Math.PI * 2;

function circularMean(angles: number[]): number | null {
  if (angles.length === 0) return null;
  let sx = 0;
  let sy = 0;
  for (const a of angles) {
    sx += Math.cos(a);
    sy += Math.sin(a);
  }
  if (Math.abs(sx) < 1e-9 && Math.abs(sy) < 1e-9) return angles[0]!;
  return Math.atan2(sy, sx);
}

const norm = (a: number) => ((a % TAU) + TAU) % TAU;

/** Spreads items around a ring in the order of their preferred angle, at least `gap` apart. */
function spread(items: { id: string; pref: number }[], minGap: number): Map<string, number> {
  const out = new Map<string, number>();
  const n = items.length;
  if (n === 0) return out;
  const sorted = [...items].sort((a, b) => norm(a.pref) - norm(b.pref) || a.id.localeCompare(b.id));
  const gap = Math.min(minGap, TAU / n);
  const angles = sorted.map((s) => norm(s.pref));
  // push forward so that consecutive items are at least `gap` apart (one pass is enough for a demo-sized graph)
  for (let i = 1; i < n; i++) if (angles[i]! - angles[i - 1]! < gap) angles[i] = angles[i - 1]! + gap;
  // if the last wrapped past the first, rotate everything back evenly
  const overflow = angles[n - 1]! - (angles[0]! + TAU - gap);
  sorted.forEach((s, i) => out.set(s.id, angles[i]! - (overflow > 0 ? overflow * (i / Math.max(1, n - 1)) : 0)));
  return out;
}

/**
 * Deterministic radial layout: the centre complaint in the middle, complaints at distance d on
 * ring 2d, and each identifier on the ring between its nearest complaint and the next ring, at
 * the mean angle of the complaints it connects. No physics, no new graph library (same approach
 * as the simulator's ring graph).
 */
export function layoutNetwork(nodes: GraphNode[], edges: GraphEdge[], width: number, height: number): Map<string, Pos> {
  const pos = new Map<string, Pos>();
  const cx = width / 2;
  const cy = height / 2;
  const complaints = nodes.filter((n) => n.kind === "complaint");
  const entities = nodes.filter((n) => n.kind === "entity");
  const maxD = Math.max(1, ...complaints.map((c) => c.distance));
  const unit = (Math.min(width, height) / 2 - 36) / (2 * maxD);
  const membersOf = new Map<string, string[]>();
  const entitiesOf = new Map<string, string[]>();
  for (const e of edges) {
    if (e.kind !== "identifier") continue;
    membersOf.set(e.target, [...(membersOf.get(e.target) ?? []), e.source]);
    entitiesOf.set(e.source, [...(entitiesOf.get(e.source) ?? []), e.target]);
  }
  const dist = new Map(complaints.map((c) => [c.id, c.distance]));
  const angle = new Map<string, number>();
  const entityIndex = new Map(entities.map((e, i) => [e.id, i]));

  for (const c of complaints) if (c.distance === 0) pos.set(c.id, { x: cx, y: cy });
  for (let d = 1; d <= maxD; d++) {
    const ring = complaints.filter((c) => c.distance === d);
    const prefs = ring.map((c, i) => {
      // prefer the angle of the complaints one ring in that share an identifier with this one
      const inner: number[] = [];
      for (const ent of entitiesOf.get(c.id) ?? []) for (const m of membersOf.get(ent) ?? []) if (dist.get(m) === d - 1 && angle.has(m)) inner.push(angle.get(m)!);
      const first = Math.min(...(entitiesOf.get(c.id) ?? []).map((e) => entityIndex.get(e) ?? 0), entities.length);
      const fallback = (TAU * (first + i / Math.max(1, ring.length))) / Math.max(1, entities.length);
      return { id: c.id, pref: circularMean(inner) ?? fallback - Math.PI / 2 };
    });
    const placed = spread(prefs, TAU / Math.max(1, ring.length));
    for (const [id, a] of placed) {
      angle.set(id, a);
      pos.set(id, { x: cx + Math.cos(a) * unit * 2 * d, y: cy + Math.sin(a) * unit * 2 * d });
    }
  }

  const rings = new Map<number, { id: string; pref: number }[]>();
  entities.forEach((e, i) => {
    const members = membersOf.get(e.id) ?? [];
    const minD = Math.min(maxD, ...members.map((m) => dist.get(m) ?? maxD));
    const outer = members.filter((m) => (dist.get(m) ?? 0) > 0 && angle.has(m)).map((m) => angle.get(m)!);
    const pref = circularMean(outer) ?? (TAU * i) / Math.max(1, entities.length) - Math.PI / 2;
    rings.set(minD, [...(rings.get(minD) ?? []), { id: e.id, pref }]);
  });
  for (const [minD, items] of rings) {
    const r = unit * (2 * minD + 1);
    const placed = spread(items, 0.5);
    for (const [id, a] of placed) pos.set(id, { x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r });
  }
  return pos;
}
