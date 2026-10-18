import { useMemo, useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { LinkBadge } from "./LinkBadge";
import { RiskBadge, StatusChip } from "@/components/complaints/ui";
import { CATEGORY_LABEL, formatDateTime, shortId } from "@/lib/complaints/format";
import { CONFIRMED_NOT_GUILT, ENTITY_LABEL, FIELD_LABEL, LINK_HINT, type GraphComplaintNode, type GraphEdge, type GraphEntityNode, type GraphNode, type GraphResponse } from "@/lib/complaints/network";
import { layoutNetwork, type Pos } from "@/lib/complaints/networkLayout";
import { cn } from "@/lib/utils/cn";

const W = 720;
const H = 520;

const ENTITY_ABBR: Record<GraphEntityNode["entity_type"], string> = {
  phone: "PH",
  upi: "UPI",
  email: "@",
  url: "URL",
  domain: "DOM",
  bank_account: "BANK",
  social_account: "SOC",
};

type Selected = { kind: "node"; id: string } | { kind: "edge"; id: string } | null;

/** Stroke style per edge: the kind of link is shown by line style AND text (legend, list), never colour alone. */
function edgeStyle(e: GraphEdge): { stroke: string; dash?: string; width: number } {
  if (e.kind === "review") return e.decision === "confirmed" ? { stroke: "var(--safe)", width: 3 } : { stroke: "var(--fg-subtle)", dash: "2 4", width: 2 };
  return e.link === "inferred" ? { stroke: "var(--warn)", dash: "6 4", width: 1.5 } : { stroke: "var(--flow)", width: 1.5 };
}

/** Identifier edges are straight; review edges arc to one side so they never hide the identifier between two complaints. */
function edgePath(e: GraphEdge, a: Pos, b: Pos): string {
  if (e.kind !== "review") return `M${a.x},${a.y} L${b.x},${b.y}`;
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const k = 0.35;
  return `M${a.x},${a.y} Q${mx - dy * k},${my + dx * k} ${b.x},${b.y}`;
}

function nodeLabel(n: GraphNode): string {
  if (n.kind === "complaint") return `Complaint ${shortId(n.complaint_id)}${n.center ? " (this complaint)" : ""}: ${n.title}`;
  return `${ENTITY_LABEL[n.entity_type]} ${n.display}, on ${n.complaint_count} complaint${n.complaint_count === 1 ? "" : "s"} shown`;
}

function edgeLinkType(e: GraphEdge) {
  return e.kind === "review" ? e.decision : e.link;
}

function EdgeEvidence({ e, byId }: { e: GraphEdge; byId: Map<string, GraphNode> }) {
  const a = byId.get(e.source);
  const b = byId.get(e.target);
  const name = (n: GraphNode | undefined) => (n ? (n.kind === "complaint" ? `#${shortId(n.complaint_id)}` : `${ENTITY_LABEL[n.entity_type]} ${n.display}`) : "?");
  if (e.kind === "identifier") {
    return (
      <div className="space-y-1">
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <LinkBadge type={e.link} /> {name(a)} → {name(b)}
        </p>
        <p className="text-sm" data-testid="edge-evidence">
          {e.evidence}
        </p>
        <p className="text-xs text-subtle">
          Field: {FIELD_LABEL[e.source_field] ?? e.source_field}. {LINK_HINT[e.link]}
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-1">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <LinkBadge type={e.decision} /> {name(a)} ↔ {name(b)}
      </p>
      <p className="text-sm" data-testid="edge-evidence">
        Investigator review: {e.decision} by <span className="mono">{shortId(e.reviewer_user_id)}</span> on {formatDateTime(e.created_at)} (original basis: {e.basis}
        {e.review_count > 1 ? `, ${e.review_count} reviews, latest shown` : ""}).
      </p>
      {e.note && <p className="whitespace-pre-wrap rounded-lg border border-line bg-chip p-2 text-sm">{e.note}</p>}
      {e.decision === "confirmed" && <p className="text-xs text-subtle">{CONFIRMED_NOT_GUILT}</p>}
    </div>
  );
}

function NodeDetail({ n, edges, byId }: { n: GraphNode; edges: GraphEdge[]; byId: Map<string, GraphNode> }) {
  const mine = edges.filter((e) => e.source === n.id || e.target === n.id);
  return (
    <div className="space-y-3">
      {n.kind === "complaint" ? (
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <StatusChip status={n.status} />
            <RiskBadge level={n.risk_level} />
            {n.center && <span className="text-xs text-intel">This complaint</span>}
          </div>
          <p className="text-sm">
            <Link to={`/investigator/complaints/${n.complaint_id}`} className="font-semibold hover:underline">
              {n.title}
            </Link>
          </p>
          <p className="text-xs text-muted">
            {CATEGORY_LABEL[n.category] ?? n.category} · {formatDateTime(n.created_at)} · <span className="mono">#{shortId(n.complaint_id)}</span> · {n.distance === 0 ? "centre" : `${n.distance} step${n.distance === 1 ? "" : "s"} away`}
          </p>
        </div>
      ) : (
        <p className="text-sm">
          <span className="font-medium">{ENTITY_LABEL[n.entity_type]}</span> <span className="mono">{n.display}</span> (masked) · given on {n.complaint_count} complaint{n.complaint_count === 1 ? "" : "s"} shown
        </p>
      )}
      <ul className="space-y-2" aria-label="Connections of the selected item">
        {mine.map((e) => (
          <li key={e.id} className="rounded-lg border border-line p-2">
            <EdgeEvidence e={e} byId={byId} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Staff only: the complaint network as SVG plus an equivalent text list of every connection and its evidence. */
export function NetworkGraph({ graph }: { graph: GraphResponse }) {
  const [sel, setSel] = useState<Selected>(null);
  const pos = useMemo(() => layoutNetwork(graph.nodes, graph.edges, W, H), [graph]);
  const byId = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph]);
  const complaints = graph.nodes.filter((n): n is GraphComplaintNode => n.kind === "complaint");
  const selNode = sel?.kind === "node" ? byId.get(sel.id) : undefined;
  const selEdge = sel?.kind === "edge" ? graph.edges.find((e) => e.id === sel.id) : undefined;
  const key = (s: Selected) => (ev: KeyboardEvent) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      setSel(s);
    }
  };
  const isSel = (kind: "node" | "edge", id: string) => sel?.kind === kind && sel.id === id;

  return (
    <div className="space-y-4" data-testid="network-graph">
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="overflow-hidden rounded-xl border border-line bg-sunken">
          <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="group" aria-label={`Network graph: ${graph.counts.complaints} complaints, ${graph.counts.entities} identifiers, ${graph.counts.edges} connections`}>
            {graph.edges.map((e) => {
              const a = pos.get(e.source);
              const b = pos.get(e.target);
              if (!a || !b) return null;
              const s = edgeStyle(e);
              return (
                <g key={e.id} data-testid="graph-edge" data-link={edgeLinkType(e)} className="cursor-pointer" onClick={() => setSel({ kind: "edge", id: e.id })}>
                  <path d={edgePath(e, a, b)} fill="none" stroke="transparent" strokeWidth={12} />
                  <path d={edgePath(e, a, b)} fill="none" stroke={s.stroke} strokeDasharray={s.dash} strokeWidth={isSel("edge", e.id) ? s.width + 2 : s.width} />
                </g>
              );
            })}
            {graph.nodes.map((n) => {
              const p = pos.get(n.id);
              if (!p) return null;
              const selected = isSel("node", n.id);
              const s: Selected = { kind: "node", id: n.id };
              return (
                <g
                  key={n.id}
                  transform={`translate(${p.x},${p.y})`}
                  role="button"
                  tabIndex={0}
                  aria-label={nodeLabel(n)}
                  aria-pressed={selected}
                  data-testid={n.kind === "complaint" ? "graph-complaint" : "graph-entity"}
                  data-id={n.kind === "complaint" ? n.complaint_id : n.id}
                  className="cursor-pointer outline-none [&:focus-visible>.ring]:opacity-100"
                  onClick={() => setSel(s)}
                  onKeyDown={key(s)}
                >
                  <title>{nodeLabel(n)}</title>
                  {n.kind === "complaint" ? (
                    <>
                      <circle className="ring opacity-0" r={n.center ? 22 : 17} fill="none" stroke="var(--intel)" strokeWidth={2} />
                      <circle r={n.center ? 17 : 12} fill={n.center ? "var(--intel-soft)" : "var(--chip-bg)"} stroke={selected ? "var(--intel)" : "var(--border-strong)"} strokeWidth={selected ? 3 : 1.5} />
                      <text y={n.center ? 32 : 27} textAnchor="middle" className="fill-[var(--fg-muted)] font-mono text-[10px]">
                        #{shortId(n.complaint_id)}
                      </text>
                    </>
                  ) : (
                    <>
                      <rect className="ring opacity-0" x={-22} y={-13} width={44} height={26} rx={7} fill="none" stroke="var(--intel)" strokeWidth={2} />
                      <rect x={-18} y={-9} width={36} height={18} rx={5} fill="var(--bg-sunken)" stroke={selected ? "var(--intel)" : n.entity_type === "domain" ? "var(--warn)" : "var(--flow)"} strokeDasharray={n.entity_type === "domain" ? "3 2" : undefined} strokeWidth={selected ? 2.5 : 1.25} />
                      <text y={3.5} textAnchor="middle" className="fill-[var(--fg)] text-[9px] font-semibold">
                        {ENTITY_ABBR[n.entity_type]}
                      </text>
                    </>
                  )}
                </g>
              );
            })}
          </svg>
        </div>
        <aside className="space-y-3 rounded-xl border border-line p-3" aria-live="polite" data-testid="graph-detail">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-subtle">Selected</h3>
          {selNode ? <NodeDetail n={selNode} edges={graph.edges} byId={byId} /> : selEdge ? <EdgeEvidence e={selEdge} byId={byId} /> : <p className="text-sm text-muted">Select a complaint, an identifier or a line to see its evidence. Every connection is also listed below the graph.</p>}
        </aside>
      </div>

      <dl className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted" aria-label="Legend" data-testid="graph-legend">
        <div className="flex items-center gap-2">
          <svg width="28" height="8" aria-hidden>
            <line x1="0" y1="4" x2="28" y2="4" stroke="var(--flow)" strokeWidth="1.5" />
          </svg>
          <dt className="font-medium text-fg">Observed</dt>
          <dd>{LINK_HINT.observed}</dd>
        </div>
        <div className="flex items-center gap-2">
          <svg width="28" height="8" aria-hidden>
            <line x1="0" y1="4" x2="28" y2="4" stroke="var(--warn)" strokeWidth="1.5" strokeDasharray="6 4" />
          </svg>
          <dt className="font-medium text-fg">Inferred</dt>
          <dd>{LINK_HINT.inferred}</dd>
        </div>
        <div className="flex items-center gap-2">
          <svg width="28" height="8" aria-hidden>
            <line x1="0" y1="4" x2="28" y2="4" stroke="var(--safe)" strokeWidth="3" />
          </svg>
          <dt className="font-medium text-fg">Confirmed</dt>
          <dd>{LINK_HINT.confirmed}</dd>
        </div>
        <div className="flex items-center gap-2">
          <svg width="28" height="8" aria-hidden>
            <line x1="0" y1="4" x2="28" y2="4" stroke="var(--fg-subtle)" strokeWidth="2" strokeDasharray="2 4" />
          </svg>
          <dt className="font-medium text-fg">Rejected</dt>
          <dd>{LINK_HINT.rejected}</dd>
        </div>
      </dl>

      <section aria-label="Connections and their evidence" className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-subtle">
          Connections ({graph.edges.length}) among {complaints.length} complaint{complaints.length === 1 ? "" : "s"}
        </h3>
        {graph.edges.length === 0 ? (
          <p className="text-sm text-muted" data-testid="graph-no-edges">
            No identifier on this complaint is shared with another complaint.
          </p>
        ) : (
          <ul className="divide-y divide-line rounded-xl border border-line" data-testid="graph-edge-list">
            {graph.edges.map((e) => (
              <li key={e.id} className={cn("px-3 py-2", isSel("edge", e.id) && "bg-hover")} data-testid="graph-edge-row" data-link={edgeLinkType(e)}>
                <EdgeEvidence e={e} byId={byId} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
