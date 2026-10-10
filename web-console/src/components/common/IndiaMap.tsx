import { useMemo, useState } from "react";
import { motion } from "framer-motion";
import { ArrowDownRight, ArrowRight, ArrowUpRight } from "lucide-react";
import { MAP_H, MAP_W, getIndiaGeo, project } from "@/lib/geo/india";
import { aggregateCities, arcMidpoint, arcPath, buildArcs, dominantKind, markerRadius, type GeoPointLite } from "@/lib/geo/arcs";
import type { Hotspot, PointKind } from "@/types/api";
import { useMotionPrefs } from "@/lib/motion/prefs";
import { cn } from "@/lib/utils/cn";

const KIND_LABEL: Record<PointKind, string> = {
  scam_call: "Scam call",
  held_transfer: "Held transfer",
  protected: "Victim protected",
  cash_out: "Cash-out",
};
const KIND_COLOR: Record<PointKind, string> = {
  scam_call: "var(--danger)",
  held_transfer: "var(--warn)",
  protected: "var(--safe)",
  cash_out: "var(--danger)",
};

interface Props {
  points: GeoPointLite[];
  hotspots?: Hotspot[];
  compact?: boolean;
  className?: string;
}

export default function IndiaMap({ points, hotspots, compact, className }: Props) {
  const { reduced } = useMotionPrefs();
  const geo = useMemo(() => getIndiaGeo(), []);
  const cities = useMemo(() => aggregateCities(points), [points]);
  const arcs = useMemo(() => buildArcs(cities), [cities]);
  const [hover, setHover] = useState<string | null>(null);
  const trendByCity = useMemo(() => new Map((hotspots ?? []).map((h) => [h.city, h])), [hotspots]);

  const placed = cities
    .map((c) => ({ c, xy: project(c.lon, c.lat) }))
    .filter((p): p is { c: (typeof cities)[number]; xy: [number, number] } => p.xy !== null);
  const active = placed.find((p) => p.c.city === hover);

  return (
    <div className={cn("relative w-full", className)}>
      <svg
        viewBox={`0 0 ${MAP_W} ${MAP_H}`}
        className="h-full w-full"
        role="img"
        aria-label={`Map of India with ${cities.length} incident ${cities.length === 1 ? "location" : "locations"} and ${arcs.length} payment ${arcs.length === 1 ? "path" : "paths"}`}
      >
        <g>
          {geo.states.map((s) => (
            <path key={s.name} d={s.d} fill="var(--map-land)" stroke="var(--map-land-stroke)" strokeWidth={0.6} />
          ))}
        </g>
        <g fill="none" strokeLinecap="round" data-testid="arcs">
          {arcs.map((a, ai) => {
            const p1 = project(a.from.lon, a.from.lat);
            const p2 = project(a.to.lon, a.to.lat);
            if (!p1 || !p2) return null;
            const d = arcPath(p1, p2);
            const w = 1.8 + Math.min(2.2, (a.from.total + a.to.total) * 0.18);
            const mid = arcMidpoint(p1, p2);
            return (
              <g key={a.id} data-arc={a.id}>
                <path d={d} stroke="var(--bg)" strokeWidth={w + 3} opacity={0.55} />
                <motion.path
                  d={d}
                  stroke="var(--flow)"
                  strokeWidth={w}
                  opacity={0.5}
                  initial={reduced ? false : { pathLength: 0 }}
                  animate={{ pathLength: 1 }}
                  transition={{ duration: 1.4, ease: "easeOut" }}
                />
                <path d={d} stroke="var(--flow)" strokeWidth={w} className={reduced ? undefined : "flow-dash"} />
                {!reduced && (
                  <circle r={compact ? 2.6 : 3.6} fill="var(--flow)" stroke="var(--bg)" strokeWidth={1}>
                    <animateMotion dur={`${3.2 + (ai % 3) * 0.6}s`} repeatCount="indefinite" path={d} />
                  </circle>
                )}
                {/* The mule layer has no geography in the simulator: the hop sits on the path, not on a map position. */}
                <g transform={`translate(${mid[0]},${mid[1]})`}>
                  <title>Mule hop (accounts have no location in the simulator)</title>
                  <circle r={compact ? 3 : 4.5} fill="var(--bg)" stroke="var(--intel)" strokeWidth={1.6} />
                </g>
              </g>
            );
          })}
        </g>
        <g>
          {placed.map(({ c, xy }) => {
            const kind = dominantKind(c.kinds);
            const color = KIND_COLOR[kind];
            const r = markerRadius(c.total, !!compact);
            const isCash = kind === "cash_out";
            return (
              <g
                key={`${c.city}-${c.state}`}
                transform={`translate(${xy[0]},${xy[1]})`}
                tabIndex={0}
                role="img"
                aria-label={`${c.city}, ${c.state}: ${Object.entries(c.kinds)
                  .map(([k, n]) => `${n} ${KIND_LABEL[k as PointKind].toLowerCase()}`)
                  .join(", ")}`}
                onMouseEnter={() => setHover(c.city)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(c.city)}
                onBlur={() => setHover(null)}
                className="cursor-pointer outline-none"
              >
                {/* cell: a soft halo scaled by how many events landed here, then the pulse and the marker */}
                <circle r={r * 1.25} fill={color} fillOpacity={0.16} stroke={color} strokeOpacity={0.5} strokeWidth={1} />
                {!reduced && <circle r={r} fill="none" stroke={color} strokeWidth={2} className="pulse-ring" />}
                {isCash ? (
                  <rect x={-r * 0.7} y={-r * 0.7} width={r * 1.4} height={r * 1.4} transform="rotate(45)" fill={color} stroke="var(--fg)" strokeWidth={1.4} />
                ) : (
                  <circle r={r * 0.62} fill={color} stroke="var(--fg)" strokeWidth={1.2} />
                )}
                <text y={4} textAnchor="middle" fontSize={compact ? 8 : 10} fontWeight={700} fill="var(--bg)" className="mono" aria-hidden>
                  {c.total > 1 ? c.total : ""}
                </text>
                {!compact && (
                  <text x={r + 4} y={4} fontSize={11} fill="var(--fg)" style={{ paintOrder: "stroke" }} stroke="var(--bg)" strokeWidth={3}>
                    {c.city}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      {active && (
        <div
          className="pointer-events-none absolute z-10 w-48 rounded-lg border border-line-strong bg-solid px-2.5 py-2 text-xs shadow-xl"
          style={{
            left: `${Math.min(70, Math.max(2, (active.xy[0] / MAP_W) * 100 + 3))}%`,
            top: `${Math.min(70, Math.max(2, (active.xy[1] / MAP_H) * 100 - 8))}%`,
          }}
          role="tooltip"
        >
          <div className="font-semibold">
            {active.c.city}, <span className="text-muted">{active.c.state}</span>
          </div>
          <ul className="mt-1 space-y-0.5 text-muted">
            {(Object.entries(active.c.kinds) as [PointKind, number][]).map(([k, n]) => (
              <li key={k} className="flex justify-between">
                <span>{KIND_LABEL[k]}</span>
                <span className="mono tnum text-fg">{n}</span>
              </li>
            ))}
          </ul>
          {trendByCity.get(active.c.city) && (
            <div className="mt-1 flex items-center gap-1 border-t border-line pt-1 text-muted">
              <TrendIcon trend={trendByCity.get(active.c.city)?.trend ?? "flat"} />
              <span>
                last window: {trendByCity.get(active.c.city)?.count} (prev {trendByCity.get(active.c.city)?.prev_count})
              </span>
            </div>
          )}
        </div>
      )}
      {!compact && <MapLegend />}
      {placed.length === 0 && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <span className="rounded-full border border-line bg-solid/80 px-3 py-1 text-xs text-muted">Waiting for events…</span>
        </div>
      )}
    </div>
  );
}

export function TrendIcon({ trend }: { trend: string }) {
  if (trend === "up") return <ArrowUpRight className="h-3.5 w-3.5 text-danger-text" aria-label="rising" />;
  if (trend === "down") return <ArrowDownRight className="h-3.5 w-3.5 text-safe" aria-label="falling" />;
  return <ArrowRight className="h-3.5 w-3.5 text-muted" aria-label="flat" />;
}

function MapLegend() {
  const items: { c: string; label: string; shape: "dot" | "diamond" }[] = [
    { c: "var(--danger)", label: "Scam call", shape: "dot" },
    { c: "var(--warn)", label: "Held transfer", shape: "dot" },
    { c: "var(--safe)", label: "Victim protected", shape: "dot" },
    { c: "var(--danger)", label: "Cash-out", shape: "diamond" },
  ];
  return (
    <ul className="absolute bottom-1 left-1 flex max-w-[calc(100%-8px)] flex-wrap gap-x-3 gap-y-1 rounded-lg border border-line-strong bg-[color-mix(in_srgb,var(--bg)_82%,transparent)] px-2.5 py-1.5 text-[11px] text-muted">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <span
            className={cn("inline-block h-2.5 w-2.5", i.shape === "dot" ? "rounded-full" : "rotate-45")}
            style={{ background: i.c }}
            aria-hidden
          />
          {i.label}
        </li>
      ))}
      <li className="flex items-center gap-1.5">
        <span className="inline-block h-0 w-4 border-t-2 border-dashed border-flow" aria-hidden />
        Payment path
      </li>
      <li className="flex items-center gap-1.5">
        <span className="inline-block h-2.5 w-2.5 rounded-full border-2 border-intel bg-bg" aria-hidden />
        Mule hop (no location)
      </li>
      <li className="flex items-center gap-1.5">
        <span className="text-[10px]" aria-hidden>
          ● ⬤
        </span>
        Size = events in that city
      </li>
    </ul>
  );
}
