import { useState } from "react";
import { Link } from "react-router-dom";
import { Bell, BellOff, Check } from "lucide-react";
import { EmptyState, Panel } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { ErrorBlock, LoadingBlock, Pager, Spinner } from "./ui";
import { complaintsApi, messageFor } from "@/lib/complaints/api";
import { formatDateTime, humanise, readableAlert } from "@/lib/complaints/format";
import { useResource } from "@/lib/complaints/useResource";
import type { Alert } from "@/lib/complaints/types";
import { cn } from "@/lib/utils/cn";

const LIMIT = 8;

/** The signed-in user's alerts with unread count and mark-as-read (POST /alerts/{id}/read). */
export function AlertsPanel({ complaintBase = "/me/complaints", onChange }: { complaintBase?: string; onChange?: () => void }) {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const res = useResource((s) => complaintsApi.alerts({ unread: unreadOnly, limit: LIMIT, offset }, s), `${unreadOnly}:${offset}`);

  const markRead = async (a: Alert) => {
    setBusy(a.id);
    setActionError(null);
    try {
      const updated = await complaintsApi.readAlert(a.id);
      if (res.data) {
        res.setData({
          ...res.data,
          unread: Math.max(0, res.data.unread - (a.read_at ? 0 : 1)),
          items: unreadOnly ? res.data.items.filter((x) => x.id !== a.id) : res.data.items.map((x) => (x.id === a.id ? updated : x)),
        });
      }
      setAnnounce("Alert marked as read.");
      onChange?.();
    } catch (e) {
      setActionError(messageFor(e));
    } finally {
      setBusy(null);
    }
  };

  const unread = res.data?.unread ?? 0;
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-2">
          Alerts
          {res.data && (
            <span className="rounded-full border border-line bg-chip px-1.5 text-[10px] font-semibold normal-case tracking-normal text-fg" data-testid="unread-count">
              {unread} unread
            </span>
          )}
        </span>
      }
      icon={<Bell className="h-3.5 w-3.5" aria-hidden />}
      actions={
        <label className="flex items-center gap-1.5 text-xs text-muted">
          <input
            type="checkbox"
            checked={unreadOnly}
            onChange={(e) => {
              setUnreadOnly(e.target.checked);
              setOffset(0);
            }}
          />
          Unread only
        </label>
      }
      data-testid="alerts-panel"
    >
      <p className="sr-only" role="status" aria-live="polite">
        {announce}
      </p>
      {actionError && (
        <p role="alert" className="mx-4 mt-3 rounded-lg border border-danger/40 bg-danger-soft p-2 text-sm">
          {actionError}
        </p>
      )}
      {res.loading && !res.data ? (
        <LoadingBlock label="Loading alerts" />
      ) : res.error && !res.data ? (
        <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />
      ) : res.data && res.data.items.length === 0 ? (
        <EmptyState icon={<BellOff className="h-5 w-5" aria-hidden />} title={unreadOnly ? "No unread alerts" : "No alerts yet"} hint="You will be told here when your complaint's status changes or an analysis finds strong scam indicators." />
      ) : (
        <ul className="divide-y divide-line">
          {res.data?.items.map((a) => (
            <li key={a.id} className={cn("flex items-start gap-3 px-4 py-3", !a.read_at && "bg-intel-soft/40")} data-testid="alert-item" data-read={a.read_at ? "true" : "false"}>
              <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", a.read_at ? "bg-line-strong" : "bg-intel")} aria-hidden />
              <div className="min-w-0 flex-1 space-y-0.5">
                <p className="text-sm">
                  <span className="sr-only">{a.read_at ? "Read. " : "Unread. "}</span>
                  {readableAlert(a.message)}
                </p>
                <p className="text-xs text-subtle">
                  {humanise(a.kind)} · {formatDateTime(a.created_at)}
                  {a.complaint_id && (
                    <>
                      {" · "}
                      <Link to={`${complaintBase}/${a.complaint_id}`} className="text-flow underline underline-offset-2">
                        Open complaint
                      </Link>
                    </>
                  )}
                </p>
              </div>
              {!a.read_at && (
                <Button size="sm" variant="ghost" onClick={() => void markRead(a)} disabled={busy === a.id} aria-label={`Mark as read: ${a.message}`} data-testid="alert-read">
                  {busy === a.id ? <Spinner /> : <Check className="h-4 w-4" aria-hidden />} Mark read
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {res.data && <Pager total={res.data.total} limit={LIMIT} offset={offset} onChange={setOffset} label="Alerts" />}
    </Panel>
  );
}
