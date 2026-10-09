import type { RingEdge, RingNode } from "@/types/api";

export interface Placed {
  id: string;
  x: number;
  y: number;
  tier: number;
}

/**
 * Deterministic tiered layout: one column per distinct tier (victims -> mules -> cash-out),
 * rows ordered by a couple of barycentre sweeps to reduce crossings. No randomness, no force
 * simulation: same input, same picture.
 */
export function layoutRing(
  nodes: Pick<RingNode, "id" | "tier">[],
  edges: Pick<RingEdge, "src" | "dst">[],
  width: number,
  height: number,
  pad = 36,
): Placed[] {
  if (nodes.length === 0) return [];
  const tiers = [...new Set(nodes.map((n) => n.tier))].sort((a, b) => a - b);
  const cols: string[][] = tiers.map((t) =>
    nodes
      .filter((n) => n.tier === t)
      .map((n) => n.id)
      .sort(),
  );
  const colOf = new Map<string, number>();
  cols.forEach((c, ci) => c.forEach((id) => colOf.set(id, ci)));

  const neighbours = new Map<string, string[]>();
  const add = (a: string, b: string) => {
    const l = neighbours.get(a);
    if (l) l.push(b);
    else neighbours.set(a, [b]);
  };
  for (const e of edges) {
    if (colOf.has(e.src) && colOf.has(e.dst)) {
      add(e.src, e.dst);
      add(e.dst, e.src);
    }
  }

  const rowIndex = () => {
    const m = new Map<string, number>();
    cols.forEach((c) => c.forEach((id, i) => m.set(id, c.length > 1 ? i / (c.length - 1) : 0.5)));
    return m;
  };
  const sweep = (order: number[]) => {
    for (const ci of order) {
      const idx = rowIndex();
      const col = cols[ci] as string[];
      const key = (id: string) => {
        const ns = neighbours.get(id) ?? [];
        if (ns.length === 0) return idx.get(id) ?? 0.5;
        return ns.reduce((s, n) => s + (idx.get(n) ?? 0.5), 0) / ns.length;
      };
      cols[ci] = [...col].sort((a, b) => key(a) - key(b) || a.localeCompare(b));
    }
  };
  const fwd = cols.map((_, i) => i);
  for (let i = 0; i < 2; i++) {
    sweep(fwd);
    sweep([...fwd].reverse());
  }

  const out: Placed[] = [];
  const nCols = cols.length;
  cols.forEach((col, ci) => {
    const x = nCols === 1 ? width / 2 : pad + (ci * (width - 2 * pad)) / (nCols - 1);
    col.forEach((id, ri) => {
      const y = col.length === 1 ? height / 2 : pad + (ri * (height - 2 * pad)) / (col.length - 1);
      out.push({ id, x, y, tier: tiers[ci] as number });
    });
  });
  return out;
}

export function edgePath(a: { x: number; y: number }, b: { x: number; y: number }): string {
  const mx = (a.x + b.x) / 2;
  return `M${a.x.toFixed(1)},${a.y.toFixed(1)} C${mx.toFixed(1)},${a.y.toFixed(1)} ${mx.toFixed(1)},${b.y.toFixed(1)} ${b.x.toFixed(1)},${b.y.toFixed(1)}`;
}
