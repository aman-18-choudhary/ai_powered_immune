import { request } from "./api";
import type { Category, RiskLevel } from "./types";

/** Complaint hotspots (Phase 10), staff only: counts on a lat/lon grid, never single complaints. */
export const CELL_SIZES = [0.1, 0.25, 0.5, 1] as const;
export type CellSize = (typeof CELL_SIZES)[number];

export interface HotspotCell {
  id: string;
  lat: number;
  lon: number;
  bounds: { south: number; west: number; north: number; east: number };
  count: number;
  by_category: Partial<Record<Category, number>>;
  by_risk: Partial<Record<RiskLevel | "not_scored", number>>;
  first_at: string | null;
  last_at: string | null;
  previous_count?: number;
  change?: number;
}

export interface HotspotsResponse {
  cell_degrees: number;
  min_count: number;
  note: string;
  filters: Record<string, unknown>;
  totals: { complaints: number; with_coordinates: number; without_coordinates: number; cells: number };
  cells: HotspotCell[];
  cells_truncated: boolean;
  places: { state: string | null; city: string | null; count: number }[];
  places_truncated: boolean;
  comparison: { previous: { from: string | null; to: string | null } | null } | null;
  truncated: boolean;
}

export interface HotspotQuery {
  from?: string;
  to?: string;
  category?: string;
  risk_level?: string;
  cell: CellSize;
  min_count: number;
  compare: boolean;
}

export function hotspotsQs(q: HotspotQuery): string {
  const p = new URLSearchParams();
  if (q.from) p.set("from", q.from);
  if (q.to) p.set("to", q.to);
  if (q.category) p.set("category", q.category);
  if (q.risk_level) p.set("risk_level", q.risk_level);
  p.set("cell", String(q.cell));
  p.set("min_count", String(q.min_count));
  if (q.compare) p.set("compare", "true");
  return p.toString();
}

export const geoApi = {
  hotspots: (q: HotspotQuery, signal?: AbortSignal) => request<HotspotsResponse>("GET", `/geo/complaints/hotspots?${hotspotsQs(q)}`, { signal }),
};
