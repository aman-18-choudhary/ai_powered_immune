import { useState, type FormEvent } from "react";
import { Filter, Inbox, ListChecks, Search, X } from "lucide-react";
import { EmptyState, Panel } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { ComplaintRows } from "@/components/complaints/ComplaintRows";
import { ErrorBlock, LoadingBlock, PageHeader, Pager, inputCls } from "@/components/complaints/ui";
import { complaintsApi, type ComplaintFilters } from "@/lib/complaints/api";
import { CATEGORY_LABEL, RISK_LABEL, STATUS_LABEL } from "@/lib/complaints/format";
import { CATEGORIES, RISK_FILTERS, STATUSES, type ComplaintStatus } from "@/lib/complaints/types";
import { useResource } from "@/lib/complaints/useResource";
import { usePageTitle } from "@/lib/hooks/usePageTitle";
import { cn } from "@/lib/utils/cn";

const LIMIT = 20;

interface Draft {
  q: string;
  status: ComplaintStatus | "";
  category: string;
  risk_level: string;
  city: string;
  state: string;
  from: string;
  to: string;
  sort: string;
}
const EMPTY: Draft = { q: "", status: "", category: "", risk_level: "", city: "", state: "", from: "", to: "", sort: "-created_at" };

/** Local calendar day bounds as ISO timestamps with the browser's offset. */
function dayBound(date: string, end: boolean): string | undefined {
  if (!date) return undefined;
  const d = new Date(`${date}T${end ? "23:59:59" : "00:00:00"}`);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

function toFilters(d: Draft, offset: number): ComplaintFilters {
  return {
    q: d.q.trim() || undefined,
    status: d.status,
    category: d.category,
    risk_level: d.risk_level,
    city: d.city.trim() || undefined,
    state: d.state.trim() || undefined,
    created_from: dayBound(d.from, false),
    created_to: dayBound(d.to, true),
    sort: d.sort,
    limit: LIMIT,
    offset,
  };
}

export default function InvestigatorQueue() {
  usePageTitle("Investigator queue");
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [applied, setApplied] = useState<Draft>(EMPTY);
  const [offset, setOffset] = useState(0);
  const [dateErr, setDateErr] = useState<string | null>(null);
  const res = useResource((s) => complaintsApi.list(toFilters(applied, offset), s), JSON.stringify([applied, offset]));
  const active = JSON.stringify(applied) !== JSON.stringify(EMPTY);

  const apply = (e?: FormEvent) => {
    e?.preventDefault();
    if (draft.from && draft.to && draft.from > draft.to) {
      setDateErr("The 'from' date must be on or before the 'to' date.");
      return;
    }
    setDateErr(null);
    setApplied(draft);
    setOffset(0);
  };
  const clear = () => {
    setDraft(EMPTY);
    setApplied(EMPTY);
    setOffset(0);
    setDateErr(null);
  };
  const set = (k: keyof Draft, v: string) => setDraft((d) => ({ ...d, [k]: v }));
  const sel = cn(inputCls, "py-1.5 text-sm");

  return (
    <div className="mx-auto max-w-6xl space-y-5 px-4 py-6 md:px-6">
      <PageHeader title="Investigator queue" subtitle="All complaints you are allowed to see, newest first. Real records." />
      <form onSubmit={apply} className="panel space-y-3 p-4" aria-label="Search and filters" noValidate>
        <div className="flex flex-wrap gap-2">
          <label htmlFor="q" className="sr-only">
            Search title, description, bank account or IFSC
          </label>
          <div className="relative min-w-[220px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" aria-hidden />
            <input id="q" value={draft.q} maxLength={100} onChange={(e) => set("q", e.target.value)} placeholder="Search title, description, account number or IFSC" className={cn(inputCls, "pl-9")} data-testid="queue-search" />
          </div>
          <Button type="submit" variant="primary" data-testid="queue-apply">
            <Filter className="h-4 w-4" aria-hidden /> Apply
          </Button>
          {active && (
            <Button type="button" variant="ghost" onClick={clear}>
              <X className="h-4 w-4" aria-hidden /> Clear
            </Button>
          )}
        </div>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <label className="space-y-1 text-xs text-muted">
            Status
            <select value={draft.status} onChange={(e) => set("status", e.target.value)} className={sel} data-testid="queue-status">
              <option value="">Any status</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-xs text-muted">
            Category
            <select value={draft.category} onChange={(e) => set("category", e.target.value)} className={sel}>
              <option value="">Any category</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABEL[c]}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-xs text-muted">
            Risk level (latest analysis)
            <select value={draft.risk_level} onChange={(e) => set("risk_level", e.target.value)} className={sel}>
              <option value="">Any</option>
              {RISK_FILTERS.map((r) => (
                <option key={r} value={r}>
                  {RISK_LABEL[r]}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-xs text-muted">
            Sort
            <select value={draft.sort} onChange={(e) => set("sort", e.target.value)} className={sel}>
              <option value="-created_at">Newest first</option>
              <option value="created_at">Oldest first</option>
              <option value="-incident_at">Incident, newest first</option>
              <option value="-amount">Amount, highest first</option>
            </select>
          </label>
          <label className="space-y-1 text-xs text-muted">
            City
            <input value={draft.city} maxLength={100} onChange={(e) => set("city", e.target.value)} className={sel} />
          </label>
          <label className="space-y-1 text-xs text-muted">
            State
            <input value={draft.state} maxLength={100} onChange={(e) => set("state", e.target.value)} className={sel} />
          </label>
          <label className="space-y-1 text-xs text-muted">
            Registered from
            <input type="date" value={draft.from} onChange={(e) => set("from", e.target.value)} className={sel} aria-invalid={dateErr ? true : undefined} />
          </label>
          <label className="space-y-1 text-xs text-muted">
            Registered to
            <input type="date" value={draft.to} onChange={(e) => set("to", e.target.value)} className={sel} aria-invalid={dateErr ? true : undefined} />
          </label>
        </div>
        {dateErr && (
          <p role="alert" className="text-xs text-danger-text">
            {dateErr}
          </p>
        )}
      </form>
      <Panel
        title={res.data ? `Complaints (${res.data.total})` : "Complaints"}
        icon={<ListChecks className="h-3.5 w-3.5" aria-hidden />}
        data-testid="queue"
      >
        {res.loading && !res.data ? (
          <LoadingBlock label="Loading complaints" />
        ) : res.error && !res.data ? (
          <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />
        ) : res.data && res.data.items.length === 0 ? (
          <EmptyState icon={<Inbox className="h-5 w-5" aria-hidden />} title={active ? "No complaints match these filters" : "No complaints yet"} hint={active ? "Clear or change a filter." : "New complaints appear here as citizens register them."} />
        ) : (
          res.data && <ComplaintRows items={res.data.items} base="/investigator/complaints" staff />
        )}
        {res.error && res.data && (
          <p role="alert" className="px-4 py-2 text-xs text-danger-text">
            {res.error}
          </p>
        )}
        {res.data && <Pager total={res.data.total} limit={LIMIT} offset={offset} onChange={setOffset} label="Complaints" />}
      </Panel>
    </div>
  );
}
