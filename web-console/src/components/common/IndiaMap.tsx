import { useMemo, useState } from "react";
import { motion } from "framer-motion";
import { ArrowDownRight, ArrowRight, ArrowUpRight } from "lucide-react";
import { MAP_H, MAP_W, getIndiaGeo, project } from "@/lib/geo/india";
import { aggregateCities, arcPath, buildArcs, dominantKind, type GeoPointLite } from "@/lib/geo/arcs";
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
        <g fill="none" strokeLinecap="round">
          {arcs.map((a) => {
            const p1 = project(a.from.lon, a.from.lat);
            const p2 = project(a.to.lon, a.to.lat);
            if (!p1 || !p2) return null;
            const d = arcPath(p1, p2);
            return (
              <g key={a.id}>
                <motion.path
                  d={d}
                  stroke="var(--flow)"
                  strokeWidth={1.4}
                  opacity={0.35}
                  initial={reduced ? false : { pathLength: 0 }}
                  animate={{ pathLength: 1 }}
                  transition={{ duration: 1.2, ease: "easeOut" }}
                />
                <path d={d} stroke="var(--flow)" strokeWidth={2} className={reduced ? undefined : "flow-dash"} />
              </g>
            );
          })}
        </g>
        <g>
          {placed.map(({ c, xy }) => {
            const kind = dominantKind(c.kinds);
            const color = KIND_COLOR[kind];
            const r = Math.min(compact ? 11 : 16, (compact ? 4 : 5) + Math.sqrt(c.total) * 2);
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
                {!reduced && <circle r={r} fill="none" stroke={color} strokeWidth={1.5} className="pulse-ring" />}
                {isCash ? (
                  <rect x={-r * 0.6} y={-r * 0.6} width={r * 1.2} height={r * 1.2} transform="rotate(45)" fill={color} fillOpacity={0.85} stroke="var(--bg)" strokeWidth={1} />
                ) : (
                  <circle r={r * 0.6} fill={color} fillOpacity={0.85} stroke="var(--bg)" strokeWidth={1} />
                )}
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
    <ul className="absolute bottom-1 left-1 flex flex-wrap gap-x-3 gap-y-1 rounded-lg border border-line bg-[color-mix(in_srgb,var(--bg)_70%,transparent)] px-2 py-1 text-[10.5px] text-muted">
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
    </ul>
  );
}
