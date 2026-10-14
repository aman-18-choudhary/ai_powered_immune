import { Link } from "react-router-dom";
import { ChevronRight, Paperclip } from "lucide-react";
import { RiskBadge, StatusChip } from "./ui";
import { CATEGORY_LABEL, formatAmount, formatDateTime, shortId } from "@/lib/complaints/format";
import type { ComplaintListItem } from "@/lib/complaints/types";

/** Rows for a complaints list (citizen history and investigator queue). */
export function ComplaintRows({ items, base, staff }: { items: ComplaintListItem[]; base: string; staff?: boolean }) {
  return (
    <ul className="divide-y divide-line" data-testid="complaint-list">
      {items.map((c) => (
        <li key={c.id}>
          <Link
            to={`${base}/${c.id}`}
            className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-hover focus-visible:bg-hover"
            data-testid="complaint-row"
          >
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="truncate text-sm font-semibold">{c.title}</span>
                <StatusChip status={c.status} />
                {c.latest_analysis && <RiskBadge level={c.latest_analysis.risk_level} score={c.latest_analysis.risk_score} />}
              </div>
              <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted">
                <span>{CATEGORY_LABEL[c.category] ?? c.category}</span>
                <span>Reported {formatDateTime(c.reported_at)}</span>
                {c.amount && <span>{formatAmount(c.amount, c.currency)}</span>}
                {(c.city || c.state) && <span>{[c.city, c.state].filter(Boolean).join(", ")}</span>}
                <span className="inline-flex items-center gap-1">
                  <Paperclip className="h-3 w-3" aria-hidden />
                  {c.evidence_count} {c.evidence_count === 1 ? "file" : "files"}
                </span>
                <span className="mono text-subtle">#{shortId(c.id)}</span>
                {staff && c.owner_user_id && <span className="mono text-subtle">owner {shortId(c.owner_user_id)}</span>}
              </p>
            </div>
            <ChevronRight className="h-4 w-4 shrink-0 text-subtle" aria-hidden />
          </Link>
        </li>
      ))}
    </ul>
  );
}
