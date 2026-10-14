import { useState } from "react";
import { Link } from "react-router-dom";
import { FilePlus2, FileText, Inbox, MessageSquareWarning } from "lucide-react";
import { EmptyState, Panel, Skeleton } from "@/components/common/Panel";
import { AlertsPanel } from "@/components/complaints/AlertsPanel";
import { ComplaintRows } from "@/components/complaints/ComplaintRows";
import { ResourcesPanel } from "@/components/complaints/ResourcesPanel";
import { ErrorBlock, LoadingBlock, PageHeader, Pager, inputCls } from "@/components/complaints/ui";
import { complaintsApi } from "@/lib/complaints/api";
import { CATEGORY_LABEL, STATUS_LABEL, formatDateTime } from "@/lib/complaints/format";
import { CATEGORIES, STATUSES, type ComplaintStatus } from "@/lib/complaints/types";
import { useResource } from "@/lib/complaints/useResource";
import { usePageTitle } from "@/lib/hooks/usePageTitle";
import { cn } from "@/lib/utils/cn";

const LIMIT = 10;

const primaryLink =
  "inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-[linear-gradient(135deg,var(--intel-strong),var(--flow))] px-4 text-sm font-semibold text-[#06090f] shadow-[0_8px_28px_-10px_var(--intel-strong)] hover:brightness-110";

function Stats({ nonce }: { nonce: number }) {
  const res = useResource((s) => complaintsApi.stats(s), `stats:${nonce}`);
  if (res.loading && !res.data) {
    return (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-busy="true" aria-label="Loading statistics">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-20" />
        ))}
      </div>
    );
  }
  if (res.error || !res.data) return <ErrorBlock message={res.error ?? "No statistics."} onRetry={res.reload} unavailable={res.unavailable} />;
  const s = res.data;
  const open = s.by_status.SUBMITTED + s.by_status.UNDER_REVIEW + s.by_status.INVESTIGATING;
  const tiles: { label: string; value: number | string; hint?: string; testid: string }[] = [
    { label: "Complaints", value: s.total, hint: s.last_complaint_at ? `Last ${formatDateTime(s.last_complaint_at)}` : "None yet", testid: "stat-total" },
    { label: "Open", value: open, hint: `${s.by_status.SUBMITTED} submitted · ${s.by_status.UNDER_REVIEW} in review · ${s.by_status.INVESTIGATING} investigating`, testid: "stat-open" },
    { label: "Closed", value: s.by_status.RESOLVED + s.by_status.REJECTED, hint: `${s.by_status.RESOLVED} resolved · ${s.by_status.REJECTED} rejected`, testid: "stat-closed" },
    { label: "Unread alerts", value: s.unread_alerts, testid: "stat-unread" },
  ];
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="stats">
      {tiles.map((t) => (
        <div key={t.label} className="panel p-3" data-testid={t.testid}>
          <dt className="text-[11px] font-semibold uppercase tracking-wide text-subtle">{t.label}</dt>
          <dd className="tnum mt-1 text-2xl font-semibold">{t.value}</dd>
          {t.hint && <dd className="mt-0.5 text-[11px] text-muted">{t.hint}</dd>}
        </div>
      ))}
    </dl>
  );
}

export default function CitizenDashboard() {
  usePageTitle("My complaints");
  const [status, setStatus] = useState<ComplaintStatus | "">("");
  const [category, setCategory] = useState("");
  const [offset, setOffset] = useState(0);
  const [nonce, setNonce] = useState(0);
  const filtered = status !== "" || category !== "";
  const list = useResource((s) => complaintsApi.list({ status, category, limit: LIMIT, offset }, s), `${status}:${category}:${offset}`);

  return (
    <div className="mx-auto max-w-6xl space-y-5 px-4 py-6 md:px-6">
      <PageHeader
        title="My complaints"
        subtitle="Your registered cyber-fraud complaints, their status and alerts. These are real records, not simulated."
        actions={
          <Link to="/me/complaints/new" className={primaryLink} data-testid="new-complaint">
            <FilePlus2 className="h-4 w-4" aria-hidden /> Register a complaint
          </Link>
        }
      />
      <Stats nonce={nonce} />
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,380px)]">
        <Panel
          title="Complaint history"
          icon={<FileText className="h-3.5 w-3.5" aria-hidden />}
        >
          <div className="flex flex-wrap gap-2 border-b border-line px-4 py-2.5">
            <label className="sr-only" htmlFor="f-status">
              Filter by status
            </label>
            <select
              id="f-status"
              value={status}
              onChange={(e) => {
                setStatus(e.target.value as ComplaintStatus | "");
                setOffset(0);
              }}
              className={cn(inputCls, "h-8 w-auto max-w-full py-0 text-xs")}
            >
              <option value="">All statuses</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </select>
            <label className="sr-only" htmlFor="f-cat">
              Filter by category
            </label>
            <select
              id="f-cat"
              value={category}
              onChange={(e) => {
                setCategory(e.target.value);
                setOffset(0);
              }}
              className={cn(inputCls, "h-8 w-auto max-w-full py-0 text-xs")}
            >
              <option value="">All categories</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABEL[c]}
                </option>
              ))}
            </select>
          </div>
          {list.loading && !list.data ? (
            <LoadingBlock label="Loading complaints" />
          ) : list.error && !list.data ? (
            <ErrorBlock message={list.error} onRetry={list.reload} unavailable={list.unavailable} />
          ) : list.data && list.data.items.length === 0 ? (
            filtered ? (
              <EmptyState icon={<Inbox className="h-5 w-5" aria-hidden />} title="No complaints match these filters" hint="Clear a filter to see more." />
            ) : (
              <div data-testid="empty-history">
                <EmptyState
                  icon={<Inbox className="h-6 w-6" aria-hidden />}
                  title="You have not registered a complaint yet"
                  hint="If you were targeted by a scam, register it here with as much detail as you have. Only the category, a title and a description are required."
                >
                  <Link to="/me/complaints/new" className={cn(primaryLink, "mt-2")}>
                    <FilePlus2 className="h-4 w-4" aria-hidden /> Register your first complaint
                  </Link>
                </EmptyState>
              </div>
            )
          ) : (
            list.data && <ComplaintRows items={list.data.items} base="/me/complaints" />
          )}
          {list.error && list.data && (
            <p role="alert" className="px-4 py-2 text-xs text-danger-text">
              {list.error}
            </p>
          )}
          {list.data && <Pager total={list.data.total} limit={LIMIT} offset={offset} onChange={setOffset} label="Complaints" />}
        </Panel>
        <div className="space-y-5">
          <AlertsPanel onChange={() => setNonce((n) => n + 1)} />
          <ResourcesPanel />
          <Link to="/citizen" className="panel flex items-start gap-3 p-4 text-sm hover:bg-hover">
            <MessageSquareWarning className="mt-0.5 h-4 w-4 shrink-0 text-intel" aria-hidden />
            <span>
              <strong className="block">Check a suspicious message</strong>
              <span className="text-muted">Citizen Shield message check (part of the simulation demo; nothing is stored).</span>
            </span>
          </Link>
        </div>
      </div>
    </div>
  );
}
