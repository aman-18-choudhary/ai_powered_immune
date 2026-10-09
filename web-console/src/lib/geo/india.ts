import { geoMercator, geoPath, type GeoPath, type GeoProjection } from "d3-geo";
import { feature } from "topojson-client";
import type { FeatureCollection, Geometry } from "geojson";
import type { Topology, GeometryCollection } from "topojson-specification";
import topo from "./india.topo.json";

export const MAP_W = 520;
export const MAP_H = 580;

export interface IndiaGeo {
  projection: GeoProjection;
  path: GeoPath;
  states: { name: string; d: string }[];
}

let cached: IndiaGeo | null = null;

/** Bundled, simplified India state outlines (no network fetch). */
export function getIndiaGeo(): IndiaGeo {
  if (cached) return cached;
  const t = topo as unknown as Topology;
  const obj = t.objects.states as GeometryCollection;
  const fc = feature(t, obj) as unknown as FeatureCollection<Geometry, { name: string }>;
  const projection = geoMercator().fitExtent(
    [
      [12, 12],
      [MAP_W - 12, MAP_H - 12],
    ],
    fc,
  );
  const path = geoPath(projection);
  const states = fc.features.map((f) => ({ name: f.properties?.name ?? "", d: path(f) ?? "" }));
  cached = { projection, path, states };
  return cached;
}

export function project(lon: number, lat: number): [number, number] | null {
  const p = getIndiaGeo().projection([lon, lat]);
  return p ? [p[0], p[1]] : null;
}
