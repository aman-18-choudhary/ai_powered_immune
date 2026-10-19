import { useMemo, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Info, MapPinned } from "lucide-react";
import { EmptyState, Panel } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { ErrorBlock, Field, LoadingBlock, PageHeader, inputCls } from "@/components/complaints/ui";
import { CATEGORY_LABEL, RISK_LABEL, formatDateTime } from "@/lib/complaints/format";
import { CELL_SIZES, geoApi, type CellSize, type HotspotCell, type HotspotQuery } from "@/lib/complaints/geo";
import { CATEGORIES, RISK_FILTERS, type Category, type RiskLevel } from "@/lib/complaints/types";
import { useResource } from "@/lib/complaints/useResource";
import { MAP_H, MAP_W, getIndiaGeo, project } from "@/lib/geo/india";
import { usePageTitle } from "@/lib/hooks/usePageTitle";
import { cn } from "@/lib/utils/cn";

interface Draft {
  from: string;
  to: string;
  category: string;
  risk_level: string;
  cell: CellSize;
  compare: boolean;
}
const EMPTY: Draft = { from: "", to: "", category: "", risk_level: "", cell: 0.25, compare: false };

/** Local calendar day bounds as ISO timestamps with the browser's offset. */
function dayBound(date: string, end: boolean): string | undefined {
  if (!date) return undefined;
  const d = new Date(`${date}T${end ? "23:59:59" : "00:00:00"}`);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

function toQuery(d: Draft): HotspotQuery {
  return { from: dayBound(d.from, false), to: dayBound(d.to, true), category: d.category || undefined, risk_level: d.risk_level || undefined, cell: d.cell, min_count: 1, compare: d.compare && !!d.from };
}

const riskName = (k: string) => (k === "not_scored" ? "Not scored" : (RISK_LABEL[k as RiskLevel] ?? k));

function changeText(c: HotspotCell): string | null {
  if (c.change === undefined) return null;
  if (c.change === 0) return `no change (${c.previous_count} before)`;
  return `${c.change > 0 ? "+" : ""}${c.change} vs ${c.previous_count} before`;
}

function CellDetail({ c }: { c: HotspotCell }) {
  return (
    <div className="space-y-1.5 text-sm" data-testid="cell-detail">
      <p>
        <span className="text-lg font-semibold">{c.count}</span> complaint{c.count === 1 ? "" : "s"} in the cell {c.bounds.south.toFixed(2)}–{c.bounds.north.toFixed(2)}° N, {c.bounds.west.toFixed(2)}–{c.bounds.east.toFixed(2)}° E
      </p>
      {changeText(c) && <p className="text-xs text-muted">Change: {changeText(c)}</p>}
      <p className="text-xs text-muted">
        {Object.entries(c.by_category)
          .map(([k, n]) => `${CATEGORY_LABEL[k as Category] ?? k} ${n}`)
          .join(" · ")}
      </p>
      <p className="text-xs text-muted">Risk: {Object.entries(c.by_risk).map(([k, n]) => `${riskName(k)} ${n}`).join(" · ")}</p>
      <p className="text-xs text-subtle">
        {formatDateTime(c.first_at)} to {formatDateTime(c.last_at)}
      </p>
    </div>
  );
}

export default function ComplaintMap() {
  usePageTitle("Complaint map");
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [applied, setApplied] = useState<Draft>(EMPTY);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const res = useResource((s) => geoApi.hotspots(toQuery(applied), s), JSON.stringify(applied));
  const geo = useMemo(() => getIndiaGeo(), []);
  const d = res.data;
  const max = Math.max(1, ...(d?.cells.map((c) => c.count) ?? [1]));
  const selected = d?.cells.find((c) => c.id === sel) ?? null;
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((x) => ({ ...x, [k]: v }));

  const apply = (e: FormEvent) => {
    e.preventDefault();
    if (draft.from && draft.to && draft.from > draft.to) return setErr("The 'from' date must be on or before the 'to' date.");
    if (draft.compare && !draft.from) return setErr("Choose a 'from' date to compare with the period before it.");
    setErr(null);
    setSel(null);
    setApplied(draft);
  };
  const key = (id: string) => (ev: KeyboardEvent) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      setSel(id);
    }
  };
  const sel_ = cn(inputCls, "py-1.5 text-sm");

  return (
    <div className="mx-auto max-w-6xl space-y-5 px-4 py-6 md:px-6">
      <PageHeader title="Complaint map" subtitle="Where complaints were reported, counted on a grid. Staff only." />
      <div className="flex items-start gap-2 rounded-xl border border-line bg-chip p-3 text-sm" data-testid="map-note">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-intel" aria-hidden />
        <div className="space-y-1">
          <p>{d?.note ?? "Counts of complaints, not of offences or offenders."}</p>
          <p className="text-xs text-muted">The map is bundled with this application: no map or tile service is contacted. Cells show their centre, never a complainant's own location.</p>
        </div>
      </div>

      <form onSubmit={apply} className="grid gap-3 rounded-xl border border-line p-4 sm:grid-cols-3 lg:grid-cols-6" noValidate data-testid="map-filters">
        <Field label="From" optional>
          {(p) => <input {...p} type="date" value={draft.from} onChange={(e) => set("from", e.target.value)} className={sel_} data-testid="map-from" />}
        </Field>
        <Field label="To" optional>
          {(p) => <input {...p} type="date" value={draft.to} onChange={(e) => set("to", e.target.value)} className={sel_} />}
        </Field>
        <Field label="Category" optional>
          {(p) => (
            <select {...p} value={draft.category} onChange={(e) => set("category", e.target.value)} className={sel_} data-testid="map-category">
              <option value="">All</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABEL[c]}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Risk" optional>
          {(p) => (
            <select {...p} value={draft.risk_level} onChange={(e) => set("risk_level", e.target.value)} className={sel_}>
              <option value="">All</option>
              {RISK_FILTERS.map((r) => (
                <option key={r} value={r}>
                  {RISK_LABEL[r]}
                </option>
              ))}
              <option value="not_scored">Not scored</option>
            </select>
          )}
        </Field>
        <Field label="Cell size" optional>
          {(p) => (
            <select {...p} value={draft.cell} onChange={(e) => set("cell", Number(e.target.value) as CellSize)} className={sel_} data-testid="map-cell">
              {CELL_SIZES.map((c) => (
                <option key={c} value={c}>
                  {c}° (about {Math.round(c * 111)} km)
                </option>
              ))}
            </select>
          )}
        </Field>
        <div className="flex flex-col justify-end gap-2">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={draft.compare} onChange={(e) => set("compare", e.target.checked)} data-testid="map-compare" /> Compare with the period before
          </label>
          <Button type="submit" variant="intel" size="sm" data-testid="map-apply">
            Apply
          </Button>
        </div>
        {err && (
          <p role="alert" className="text-sm text-danger-text sm:col-span-3 lg:col-span-6" data-testid="map-error">
            {err}
          </p>
        )}
      </form>

      {res.loading && !d ? (
        <LoadingBlock label="Counting complaints" />
      ) : res.error && !d ? (
        <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />
      ) : d ? (
        <>
          <p className="text-sm text-muted" data-testid="map-totals">
            {d.totals.complaints} complaint{d.totals.complaints === 1 ? "" : "s"}: {d.totals.with_coordinates} with coordinates in {d.totals.cells} cell{d.totals.cells === 1 ? "" : "s"}, {d.totals.without_coordinates} without coordinates.
          </p>
          {(d.truncated || d.cells_truncated) && (
            <p className="flex items-center gap-2 text-xs text-muted" data-testid="map-truncated">
              <AlertTriangle className="h-3.5 w-3.5 text-warn" aria-hidden /> Only part of the data is shown (row or cell limit reached).
            </p>
          )}
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
            <Panel title="Hotspots" icon={<MapPinned className="h-3.5 w-3.5" aria-hidden />} data-testid="map-panel">
              {d.cells.length === 0 ? (
                <EmptyState icon={<MapPinned className="h-5 w-5" aria-hidden />} title="No complaints with coordinates" hint="Complaints only have coordinates when the complainant chose to share a location." />
              ) : (
                <svg viewBox={`0 0 ${MAP_W} ${MAP_H}`} className="mx-auto h-auto w-full max-w-[520px]" role="group" aria-label={`Map of India with ${d.cells.length} complaint cells`}>
                  {geo.states.map((s) => (
                    <path key={s.name} d={s.d} fill="var(--map-land)" stroke="var(--map-land-stroke)" strokeWidth={0.6} />
                  ))}
                  {d.cells.map((c) => {
                    const xy = project(c.lon, c.lat);
                    if (!xy) return null;
                    const r = 4 + 14 * Math.sqrt(c.count / max);
                    const color = c.change === undefined || c.change === 0 ? "var(--intel)" : c.change > 0 ? "var(--danger)" : "var(--safe)";
                    const label = `${c.count} complaint${c.count === 1 ? "" : "s"} near ${c.lat.toFixed(2)}, ${c.lon.toFixed(2)}${changeText(c) ? `, ${changeText(c)}` : ""}`;
                    return (
                      <g key={c.id} role="button" tabIndex={0} aria-label={label} aria-pressed={sel === c.id} onClick={() => setSel(c.id)} onKeyDown={key(c.id)} className="cursor-pointer outline-none [&:focus-visible>circle:first-child]:stroke-[var(--fg)]" data-testid="map-cell-dot" data-count={c.count}>
                        <title>{label}</title>
                        <circle cx={xy[0]} cy={xy[1]} r={r} fill={color} fillOpacity={0.35} stroke={color} strokeWidth={sel === c.id ? 3 : 1.5} />
                        <text x={xy[0]} y={xy[1] + 3} textAnchor="middle" className="fill-[var(--fg)] text-[9px] font-semibold">
                          {c.count}
                        </text>
                      </g>
                    );
                  })}
                </svg>
              )}
              {d.comparison && (
                <p className="border-t border-line px-4 py-2 text-xs text-muted" data-testid="map-legend">
                  Red: more than in the period before ({formatDateTime(d.comparison.previous?.from ?? null)} to {formatDateTime(d.comparison.previous?.to ?? null)}); green: fewer; blue: unchanged.
                </p>
              )}
            </Panel>
            <div className="space-y-5">
              <Panel title="Selected cell" data-testid="cell-panel">
                <div className="p-4">{selected ? <CellDetail c={selected} /> : <p className="text-sm text-muted">Select a circle on the map or a row below.</p>}</div>
              </Panel>
              <Panel title="Without coordinates" data-testid="places-panel">
                {d.places.length === 0 ? (
                  <p className="p-4 text-sm text-muted">Every matching complaint has coordinates.</p>
                ) : (
                  <ul className="divide-y divide-line text-sm">
                    {d.places.map((p) => (
                      <li key={`${p.state}|${p.city}`} className="flex justify-between px-4 py-2" data-testid="place-row">
                        <span>{[p.city, p.state].filter(Boolean).join(", ") || "No place given"}</span>
                        <span className="font-medium">{p.count}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
            </div>
          </div>
          {d.cells.length > 0 && (
            <Panel title="Cells by count">
              <ul className="divide-y divide-line text-sm" data-testid="cell-list">
                {d.cells.slice(0, 20).map((c) => (
                  <li key={c.id}>
                    <button type="button" onClick={() => setSel(c.id)} className={cn("flex w-full flex-wrap justify-between gap-2 px-4 py-2 text-left hover:bg-hover", sel === c.id && "bg-hover")} data-testid="cell-row">
                      <span>
                        {c.lat.toFixed(2)}° N, {c.lon.toFixed(2)}° E
                      </span>
                      <span className="text-muted">
                        <span className="font-medium text-fg">{c.count}</span>
                        {changeText(c) ? ` · ${changeText(c)}` : ""}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              <p className="border-t border-line px-4 py-2 text-xs text-subtle">
                To read the complaints in an area, filter the <Link to="/investigator" className="text-intel underline">queue</Link> by city or state.
              </p>
            </Panel>
          )}
        </>
      ) : null}
    </div>
  );
}
