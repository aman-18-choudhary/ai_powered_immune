import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, EyeOff, Info, Network, Share2 } from "lucide-react";
import { EmptyState, Panel } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { LinkBadge } from "@/components/network/LinkBadge";
import { ErrorBlock, LoadingBlock, Pager, RiskBadge, Spinner, StatusChip, inputCls } from "./ui";
import { messageFor } from "@/lib/complaints/api";
import { CATEGORY_LABEL, formatDateTime, shortId } from "@/lib/complaints/format";
import { CONFIRMED_NOT_GUILT, DISCLAIMER, ENTITY_LABEL, FIELD_LABEL, IFSC_LABEL, LINK_LABEL, networkApi, type Decision, type RelatedItem } from "@/lib/complaints/network";
import { useResource } from "@/lib/complaints/useResource";
import { cn } from "@/lib/utils/cn";

const LIMIT = 10;

interface Pending {
  item: RelatedItem;
  decision: Decision;
}

function SharedList({ item }: { item: RelatedItem }) {
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Shared identifiers">
      {item.shared.map((s) => (
        <li key={`${s.type}:${s.display}`} className="rounded-lg border border-line bg-chip px-2 py-1 text-xs" data-testid="shared-identifier">
          <span className="font-medium">{ENTITY_LABEL[s.type]}</span> <span className="mono">{s.display}</span>
          <span className="text-subtle">
            {" "}
            · {FIELD_LABEL[s.this_field] ?? s.this_field} here, {FIELD_LABEL[s.other_field] ?? s.other_field} there
            {s.link === "inferred" ? " · inferred (domain only)" : ""}
            {s.ifsc ? ` · ${IFSC_LABEL[s.ifsc]}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Staff only: complaints that share an identifier with this one (GET /complaints/{id}/related). */
export function RelatedPanel({ complaintId }: { complaintId: string }) {
  const [offset, setOffset] = useState(0);
  const res = useResource((s) => networkApi.related(complaintId, { limit: LIMIT, offset }, s), `${complaintId}:${offset}`);
  const [pending, setPending] = useState<Pending | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const d = res.data;

  const open = (item: RelatedItem, decision: Decision) => {
    setPending({ item, decision });
    setNote("");
    setError(null);
  };

  const submit = async () => {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      const n = note.trim();
      await networkApi.review({ complaint_a: complaintId, complaint_b: pending.item.complaint.id, decision: pending.decision, ...(n ? { note: n } : {}) });
      setAnnounce(`Link ${pending.decision === "confirmed" ? "confirmed" : "rejected"} for complaint ${shortId(pending.item.complaint.id)}. Your review is recorded.`);
      setPending(null);
      res.reload();
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  };

  const body = () => {
    if (res.loading && !d) return <LoadingBlock label="Loading related complaints" />;
    if (res.error && !d) return <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />;
    if (!d) return null;
    if (d.linking === "disabled") {
      return (
        <p className="m-4 flex items-start gap-2 rounded-xl border border-line bg-chip p-3 text-sm" data-testid="related-disabled">
          <EyeOff className="mt-0.5 h-4 w-4 shrink-0 text-subtle" aria-hidden />
          {d.linking_message ?? "Linking is off for this complaint at the complainant's request."}
        </p>
      );
    }
    if (d.items.length === 0) return <EmptyState icon={<Network className="h-5 w-5" aria-hidden />} title="No related complaints" hint="No other complaint shares an identifier with this one." />;
    return (
      <>
        <ul className="divide-y divide-line" data-testid="related-list">
          {d.items.map((item) => (
            <li key={item.complaint.id} className="space-y-2 px-4 py-3" data-testid="related-item">
              <div className="flex flex-wrap items-center gap-2">
                <LinkBadge type={item.link_type} />
                {item.review && item.basis !== item.link_type && <span className="text-[11px] text-subtle">(was {LINK_LABEL[item.basis].toLowerCase()})</span>}
                <StatusChip status={item.complaint.status} />
                <RiskBadge level={item.complaint.risk_level} />
              </div>
              <p className="text-sm">
                <Link to={`/investigator/complaints/${item.complaint.id}`} className="font-semibold hover:underline">
                  {item.complaint.title}
                </Link>{" "}
                <span className="text-xs text-muted">
                  · {CATEGORY_LABEL[item.complaint.category] ?? item.complaint.category} · {formatDateTime(item.complaint.created_at)} · <span className="mono">#{shortId(item.complaint.id)}</span>
                </span>
              </p>
              <SharedList item={item} />
              {item.review && (
                <p className="text-xs text-muted" data-testid="related-review">
                  {item.review.decision === "confirmed" ? "Confirmed" : "Rejected"} by <span className="mono">{shortId(item.review.reviewer_user_id)}</span> on {formatDateTime(item.review.created_at)}
                  {item.review.review_count && item.review.review_count > 1 ? ` (${item.review.review_count} reviews, latest shown)` : ""}
                  {item.review.note ? <span className="mt-1 block whitespace-pre-wrap rounded-lg border border-line bg-chip p-2 text-fg">{item.review.note}</span> : null}
                </p>
              )}
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="safe" onClick={() => open(item, "confirmed")} disabled={item.link_type === "confirmed"} data-testid="link-confirm">
                  Confirm link
                </Button>
                <Button size="sm" variant="secondary" onClick={() => open(item, "rejected")} disabled={item.link_type === "rejected"} data-testid="link-reject">
                  Reject link
                </Button>
              </div>
            </li>
          ))}
        </ul>
        {d.truncated && (
          <p className="flex items-center gap-2 border-t border-line px-4 py-2 text-xs text-muted" data-testid="related-truncated">
            <AlertTriangle className="h-3.5 w-3.5 text-warn" aria-hidden /> More shared identifiers exist than were read; the list is incomplete.
          </p>
        )}
        <Pager total={d.total} limit={LIMIT} offset={offset} onChange={setOffset} label="Related complaints" />
      </>
    );
  };

  return (
    <Panel
      title="Related complaints"
      icon={<Network className="h-3.5 w-3.5" aria-hidden />}
      data-testid="related-panel"
      actions={
        <Link to={`/investigator/network?complaint=${encodeURIComponent(complaintId)}`} className="inline-flex items-center gap-1 text-xs text-intel hover:underline" data-testid="open-network">
          <Share2 className="h-3.5 w-3.5" aria-hidden /> Network view
        </Link>
      }
    >
      <p className="flex items-start gap-2 border-b border-line px-4 py-2 text-xs text-muted" data-testid="related-disclaimer">
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        <span>
          {d?.disclaimer ?? DISCLAIMER} {CONFIRMED_NOT_GUILT}
        </span>
      </p>
      {body()}
      <p role="status" aria-live="polite" className="sr-only" data-testid="related-announce">
        {announce}
      </p>
      <Dialog open={pending !== null} onOpenChange={(o) => !o && !busy && setPending(null)}>
        <DialogContent className="w-[min(94vw,480px)]">
          <DialogTitle className="text-lg font-semibold">{pending?.decision === "confirmed" ? "Confirm this link?" : "Reject this link?"}</DialogTitle>
          <DialogDescription className="mt-2 text-sm text-muted">
            {pending &&
              `Your review of this complaint and "${pending.item.complaint.title}" is recorded with your user ID and kept in the history. ${
                pending.decision === "confirmed" ? "Confirming says the two complaints appear to be part of the same case; it does not change either complaint and is not a finding that anyone committed a crime." : "Rejecting says the shared identifiers are not a meaningful link; the identifiers stay on both complaints."
              }`}
          </DialogDescription>
          <label htmlFor="link-note" className="mt-4 block text-sm font-medium">
            Note <span className="text-xs font-normal text-subtle">(optional, visible to investigators)</span>
          </label>
          <textarea id="link-note" rows={3} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} className={cn(inputCls, "mt-1 resize-y")} disabled={busy} data-testid="link-note" />
          <p className="text-xs text-subtle">{note.length}/500</p>
          {error && (
            <p role="alert" className="mt-2 rounded-lg border border-danger/40 bg-danger-soft p-2 text-sm" data-testid="link-error">
              {error}
            </p>
          )}
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setPending(null)} disabled={busy}>
              Cancel
            </Button>
            <Button variant={pending?.decision === "confirmed" ? "safe" : "primary"} onClick={() => void submit()} disabled={busy} data-testid="link-submit">
              {busy && <Spinner />} {pending?.decision === "confirmed" ? "Confirm link" : "Reject link"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </Panel>
  );
}
