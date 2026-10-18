import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertTriangle, EyeOff, Info, Network as NetworkIcon, Search, Share2, Waypoints } from "lucide-react";
import { EmptyState, Panel } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { NetworkGraph } from "@/components/network/NetworkGraph";
import { ErrorBlock, Field, LoadingBlock, PageHeader, Pager, RiskBadge, Spinner, StatusChip, inputCls } from "@/components/complaints/ui";
import { messageFor } from "@/lib/complaints/api";
import { CATEGORY_LABEL, formatDateTime, shortId } from "@/lib/complaints/format";
import { CONFIRMED_NOT_GUILT, DISCLAIMER, ENTITY_LABEL, ENTITY_TYPES, FIELD_LABEL, networkApi, type EntityType, type SearchResponse } from "@/lib/complaints/network";
import { useResource } from "@/lib/complaints/useResource";
import { usePageTitle } from "@/lib/hooks/usePageTitle";
import { cn } from "@/lib/utils/cn";

const CLUSTER_LIMIT = 10;
const SEARCH_LIMIT = 20;
const ID_RE = /^[A-Za-z0-9-]{1,64}$/;

function GraphSection({ complaintId }: { complaintId: string }) {
  const [depth, setDepth] = useState<1 | 2>(1);
  const res = useResource((s) => networkApi.graph(complaintId, depth, s), `${complaintId}:${depth}`);
  const g = res.data;
  return (
    <Panel
      title={`Network of complaint #${shortId(complaintId)}`}
      icon={<Share2 className="h-3.5 w-3.5" aria-hidden />}
      data-testid="graph-panel"
      actions={
        <div role="group" aria-label="Graph depth" className="flex gap-1">
          {([1, 2] as const).map((d) => (
            <Button key={d} size="sm" variant={depth === d ? "intel" : "ghost"} aria-pressed={depth === d} onClick={() => setDepth(d)} data-testid={`depth-${d}`}>
              {d === 1 ? "Direct links" : "Two steps"}
            </Button>
          ))}
        </div>
      }
    >
      <div className="space-y-3 p-4">
        <p className="text-xs text-muted">
          <Link to={`/investigator/complaints/${complaintId}`} className="text-intel hover:underline">
            Open the complaint
          </Link>{" "}
          · Identifiers come from the structured fields only (phone, UPI ID, email, link, bank account, social account) and are shown masked.
        </p>
        {res.loading && !g ? (
          <LoadingBlock label="Loading the network" />
        ) : res.error && !g ? (
          <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />
        ) : g && g.linking === "disabled" ? (
          <p className="flex items-start gap-2 rounded-xl border border-line bg-chip p-3 text-sm" data-testid="graph-disabled">
            <EyeOff className="mt-0.5 h-4 w-4 shrink-0 text-subtle" aria-hidden />
            {g.linking_message ?? "Linking is off for this complaint at the complainant's request."}
          </p>
        ) : g ? (
          <>
            {g.truncated && (
              <p className="flex items-center gap-2 rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-xs" data-testid="graph-truncated">
                <AlertTriangle className="h-3.5 w-3.5 text-warn" aria-hidden /> The network is larger than the display limits ({g.limits.complaints} complaints, {g.limits.entities} identifiers, {g.limits.edges} connections); only part of it is shown.
              </p>
            )}
            <NetworkGraph graph={g} />
          </>
        ) : null}
      </div>
    </Panel>
  );
}

function OpenComplaint({ initial, onOpen }: { initial: string; onOpen: (id: string) => void }) {
  const [v, setV] = useState(initial);
  const [err, setErr] = useState<string | undefined>();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const id = v.trim();
    if (!ID_RE.test(id)) {
      setErr("Enter a complaint ID (letters, digits and dashes).");
      return;
    }
    setErr(undefined);
    onOpen(id);
  };
  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2" data-testid="open-complaint-form" noValidate>
      <Field label="Complaint ID" error={err} className="min-w-[16rem] flex-1">
        {(p) => <input {...p} value={v} onChange={(e) => setV(e.target.value)} className={cn(inputCls, "mono")} autoComplete="off" spellCheck={false} />}
      </Field>
      <Button type="submit" variant="intel">
        Show network
      </Button>
    </form>
  );
}

function Clusters({ onOpen }: { onOpen: (id: string) => void }) {
  const [offset, setOffset] = useState(0);
  const res = useResource((s) => networkApi.clusters({ limit: CLUSTER_LIMIT, offset }, s), String(offset));
  const d = res.data;
  return (
    <Panel title="Clusters" icon={<Waypoints className="h-3.5 w-3.5" aria-hidden />} data-testid="clusters-panel">
      <p className="border-b border-line px-4 py-2 text-xs text-muted">{d?.basis ?? "Groups of complaints connected by exactly shared identifiers. Domain-only matches do not join clusters."}</p>
      {res.loading && !d ? (
        <LoadingBlock label="Loading clusters" />
      ) : res.error && !d ? (
        <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />
      ) : d && d.items.length === 0 ? (
        <EmptyState icon={<Waypoints className="h-5 w-5" aria-hidden />} title="No clusters" hint="No two complaints share an identifier." />
      ) : d ? (
        <>
          <ul className="divide-y divide-line" data-testid="cluster-list">
            {d.items.map((c) => (
              <li key={c.id} className="space-y-2 px-4 py-3" data-testid="cluster">
                <p className="text-sm">
                  <span className="font-semibold">{c.size} complaints</span>
                  <span className="text-xs text-muted">
                    {" "}
                    · shared:{" "}
                    {Object.entries(c.connecting_types)
                      .map(([t, n]) => `${ENTITY_LABEL[t as EntityType] ?? t} ×${n}`)
                      .join(", ")}{" "}
                    · {formatDateTime(c.earliest_created_at)} to {formatDateTime(c.latest_created_at)}
                  </span>
                </p>
                <ul className="flex flex-wrap gap-1.5">
                  {c.members.map((m) => (
                    <li key={m.id}>
                      <button type="button" onClick={() => onOpen(m.id)} className="rounded-lg border border-line bg-chip px-2 py-1 text-left text-xs hover:bg-hover" data-testid="cluster-member">
                        <span className="mono">#{shortId(m.id)}</span> {m.title}
                      </button>
                    </li>
                  ))}
                </ul>
                {c.members_truncated && <p className="text-xs text-subtle">Only the first {c.members.length} members are listed.</p>}
              </li>
            ))}
          </ul>
          {d.truncated && (
            <p className="flex items-center gap-2 border-t border-line px-4 py-2 text-xs text-muted" data-testid="clusters-truncated">
              <AlertTriangle className="h-3.5 w-3.5 text-warn" aria-hidden /> More shared identifiers exist than were read; the clusters may be incomplete.
            </p>
          )}
          <Pager total={d.total} limit={CLUSTER_LIMIT} offset={offset} onChange={setOffset} label="Clusters" />
        </>
      ) : null}
    </Panel>
  );
}

/** Identifier search: the value is sent in a POST body and kept only in this component's state (never the URL). */
function IdentifierSearch({ onOpen }: { onOpen: (id: string) => void }) {
  const [type, setType] = useState<EntityType>("upi");
  const [value, setValue] = useState("");
  const [platform, setPlatform] = useState("");
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SearchResponse | null>(null);

  const run = async (off: number) => {
    setBusy(true);
    setError(null);
    try {
      const r = await networkApi.search({ type, value: value.trim(), ...(type === "social_account" ? { platform: platform.trim() } : {}), limit: SEARCH_LIMIT, offset: off });
      setResult(r);
      setOffset(off);
    } catch (e) {
      setError(messageFor(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!value.trim()) {
      setError("Enter an identifier to search for.");
      return;
    }
    void run(0);
  };

  return (
    <Panel title="Identifier search" icon={<Search className="h-3.5 w-3.5" aria-hidden />} data-testid="search-panel">
      <form onSubmit={submit} className="grid gap-3 p-4 sm:grid-cols-[10rem_minmax(0,1fr)_auto]" noValidate data-testid="search-form">
        <Field label="Type">
          {(p) => (
            <select {...p} value={type} onChange={(e) => setType(e.target.value as EntityType)} className={cn(inputCls, "py-1.5 text-sm")} data-testid="search-type">
              {ENTITY_TYPES.map((t) => (
                <option key={t} value={t}>
                  {ENTITY_LABEL[t]}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Identifier" hint="Sent in the request body, never in the address bar.">
          {(p) => <input {...p} value={value} onChange={(e) => setValue(e.target.value)} className={cn(inputCls, "mono")} autoComplete="off" spellCheck={false} maxLength={2048} data-testid="search-value" />}
        </Field>
        <div className="flex items-end">
          <Button type="submit" variant="intel" disabled={busy} data-testid="search-submit">
            {busy && <Spinner />} Search
          </Button>
        </div>
        {type === "social_account" && (
          <Field label="Platform" className="sm:col-span-2">
            {(p) => <input {...p} value={platform} onChange={(e) => setPlatform(e.target.value)} className={inputCls} autoComplete="off" data-testid="search-platform" />}
          </Field>
        )}
      </form>
      {error && (
        <p role="alert" className="mx-4 mb-4 rounded-lg border border-danger/40 bg-danger-soft p-2 text-sm" data-testid="search-error">
          {error}
        </p>
      )}
      {result && (
        <div className="border-t border-line" data-testid="search-result">
          <p className="px-4 py-2 text-xs text-muted">
            {result.total} complaint{result.total === 1 ? "" : "s"} with {ENTITY_LABEL[result.entity.type]} <span className="mono">{result.entity.display}</span>. {result.disclaimer}
          </p>
          {result.items.length > 0 && (
            <ul className="divide-y divide-line">
              {result.items.map((c) => (
                <li key={c.id} className="flex flex-wrap items-center gap-2 px-4 py-2 text-sm" data-testid="search-item">
                  <StatusChip status={c.status} />
                  <RiskBadge level={c.risk_level} />
                  <Link to={`/investigator/complaints/${c.id}`} className="font-medium hover:underline">
                    {c.title}
                  </Link>
                  <span className="text-xs text-muted">
                    {CATEGORY_LABEL[c.category] ?? c.category} · {FIELD_LABEL[c.source_field] ?? c.source_field} · <span className="mono">#{shortId(c.id)}</span>
                  </span>
                  <Button size="sm" variant="ghost" onClick={() => onOpen(c.id)}>
                    Network
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <Pager total={result.total} limit={SEARCH_LIMIT} offset={offset} onChange={(o) => void run(o)} label="Search results" />
        </div>
      )}
    </Panel>
  );
}

/** Staff only. Every line is a shared identifier or an investigator's review; none is proof of guilt. */
export default function Network() {
  usePageTitle("Complaint network");
  const [params, setParams] = useSearchParams();
  const raw = params.get("complaint") ?? "";
  const complaintId = ID_RE.test(raw) ? raw : "";
  const open = (id: string) => {
    setParams({ complaint: id });
    window.scrollTo?.({ top: 0 });
  };
  return (
    <div className="mx-auto max-w-6xl space-y-5 px-4 py-6 md:px-6">
      <PageHeader title="Complaint network" subtitle="Complaints connected by identical identifiers in their structured fields, with an investigator's review where one exists." />
      <div className="flex items-start gap-2 rounded-xl border border-warn/40 bg-warn-soft p-3 text-sm" data-testid="network-disclaimer">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
        <div className="space-y-1">
          <p className="font-medium">{DISCLAIMER}</p>
          <p className="text-muted">{CONFIRMED_NOT_GUILT} Complaints whose complainants turned off case matching, and deleted complaints, are never shown or counted here.</p>
        </div>
      </div>
      <OpenComplaint key={complaintId} initial={complaintId} onOpen={open} />
      {complaintId ? <GraphSection key={complaintId} complaintId={complaintId} /> : <EmptyState icon={<NetworkIcon className="h-5 w-5" aria-hidden />} title="No complaint selected" hint="Enter a complaint ID, or pick a complaint from a cluster or a search result." />}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Clusters onOpen={open} />
        <IdentifierSearch onOpen={open} />
      </div>
    </div>
  );
}
