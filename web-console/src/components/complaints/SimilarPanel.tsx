import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Dna, EyeOff, Info, Link2, RefreshCw } from "lucide-react";
import { EmptyState, Panel } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { ErrorBlock, LoadingBlock, RiskBadge, StatusChip } from "./ui";
import { CATEGORY_LABEL, formatDateTime, humanise, shortId } from "@/lib/complaints/format";
import { BAND_LABEL, DNA_DISCLAIMER, dnaApi, type SimilarItem } from "@/lib/complaints/dna";
import { LINK_LABEL } from "@/lib/complaints/network";
import { useResource } from "@/lib/complaints/useResource";
import { cn } from "@/lib/utils/cn";

const LIMIT = 10;
const RETRY_MS = 4000;

function Score({ item }: { item: SimilarItem }) {
  const pct = Math.round(Math.max(0, Math.min(1, item.similarity)) * 100);
  return (
    <span className="inline-flex items-center gap-2" data-testid="similar-score">
      <span className="relative h-1.5 w-16 overflow-hidden rounded-full bg-chip" aria-hidden>
        <span className={cn("absolute inset-y-0 left-0 rounded-full", item.band === "strong" ? "bg-intel" : "bg-flow")} style={{ width: `${pct}%` }} />
      </span>
      <span className="text-xs">
        <span className="font-medium">{BAND_LABEL[item.band]}</span> <span className="mono text-muted">{item.similarity.toFixed(2)}</span>
      </span>
    </span>
  );
}

/** Staff only: complaints described in similar words (GET /complaints/{id}/similar). */
export function SimilarPanel({ complaintId }: { complaintId: string }) {
  const [nonce, setNonce] = useState(0);
  const res = useResource((s) => dnaApi.similar(complaintId, LIMIT, s), `${complaintId}:${nonce}`);
  const d = res.data;
  // the model loads in the background after a server start: poll a few times while it says so
  const [tries, setTries] = useState(0);
  useEffect(() => {
    if (d?.status !== "loading" || tries >= 8) return;
    const t = window.setTimeout(() => {
      setTries((n) => n + 1);
      setNonce((n) => n + 1);
    }, RETRY_MS);
    return () => window.clearTimeout(t);
  }, [d?.status, tries]);

  const body = () => {
    if (res.loading && !d) return <LoadingBlock label="Comparing descriptions" />;
    if (res.error && !d) return <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />;
    if (!d) return null;
    if (d.matching === "disabled") {
      return (
        <p className="m-4 flex items-start gap-2 rounded-xl border border-line bg-chip p-3 text-sm" data-testid="similar-disabled">
          <EyeOff className="mt-0.5 h-4 w-4 shrink-0 text-subtle" aria-hidden />
          {d.matching_message ?? "Case matching is off for this complaint at the complainant's request."}
        </p>
      );
    }
    if (d.status !== "ok") {
      return (
        <div className="m-4 space-y-2 rounded-xl border border-line bg-chip p-3 text-sm" data-testid="similar-status" data-status={d.status}>
          <p>{d.message ?? humanise(String(d.status))}</p>
          {d.status === "loading" && (
            <Button size="sm" variant="ghost" onClick={() => setNonce((n) => n + 1)}>
              <RefreshCw className="h-3.5 w-3.5" aria-hidden /> Check again
            </Button>
          )}
        </div>
      );
    }
    if (d.items.length === 0)
      return <EmptyState icon={<Dna className="h-5 w-5" aria-hidden />} title="No similar complaints" hint={`No other complaint is described in similar words (similarity ${d.thresholds.similar.toFixed(2)} or more).`} />;
    return (
      <>
        <ul className="divide-y divide-line" data-testid="similar-list">
          {d.items.map((item) => (
            <li key={item.complaint.id} className="space-y-1.5 px-4 py-3" data-testid="similar-item" data-band={item.band}>
              <div className="flex flex-wrap items-center gap-2">
                <Score item={item} />
                <StatusChip status={item.complaint.status} />
                <RiskBadge level={item.complaint.risk_level} />
                {item.identifier_link && (
                  <span className="inline-flex items-center gap-1 rounded-md border border-line px-1.5 py-0.5 text-[11px]" data-testid="similar-identifier-link">
                    <Link2 className="h-3 w-3" aria-hidden /> Also shares an identifier ({LINK_LABEL[item.identifier_link].toLowerCase()})
                  </span>
                )}
              </div>
              <p className="text-sm">
                <Link to={`/investigator/complaints/${item.complaint.id}`} className="font-semibold hover:underline">
                  {item.complaint.title}
                </Link>{" "}
                <span className="text-xs text-muted">
                  · {CATEGORY_LABEL[item.complaint.category] ?? item.complaint.category} · {formatDateTime(item.complaint.created_at)} · <span className="mono">#{shortId(item.complaint.id)}</span>
                </span>
              </p>
              <p className="text-xs text-muted" data-testid="similar-explanation">
                {item.explanation}
              </p>
            </li>
          ))}
        </ul>
        {d.total > d.items.length && (
          <p className="border-t border-line px-4 py-2 text-xs text-muted" data-testid="similar-more">
            Showing the {d.items.length} most similar of {d.total}.
          </p>
        )}
      </>
    );
  };

  return (
    <Panel title="Similar complaints (Fraud DNA)" icon={<Dna className="h-3.5 w-3.5" aria-hidden />} data-testid="similar-panel">
      <div className="space-y-1 border-b border-line px-4 py-2 text-xs text-muted" data-testid="similar-disclaimer">
        <p className="flex items-start gap-2">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>
            {d?.disclaimer ?? DNA_DISCLAIMER} Similar wording is not a shared identifier: confirmed and identifier links are under Related complaints.
          </span>
        </p>
        {d?.model && (
          <p className="pl-5 text-subtle">
            Local model <span className="mono">{d.model.id}</span>; scores are cosine similarity (not probabilities), thresholds chosen on synthetic data.
          </p>
        )}
      </div>
      {body()}
    </Panel>
  );
}
