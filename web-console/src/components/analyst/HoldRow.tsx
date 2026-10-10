import { memo } from "react";
import { AlertTriangle } from "lucide-react";
import { CountdownRing } from "@/components/common/CountdownRing";
import { DecisionBadge, ScoreMeter } from "@/components/common/Decision";
import { bankLabel, formatInr, reasonLabel } from "@/lib/format";
import { deadlineInfo, isOpen, overdueBy } from "@/lib/analyst/queue";
import type { HoldView } from "@/types/api";
import { cn } from "@/lib/utils/cn";

export const ROW_HEIGHT = 96;

interface Props {
  hold: HoldView;
  simT: number;
  selected: boolean;
  pendingLabel?: string;
  banks: { bank_a: string; bank_b: string } | null;
  onSelect: (ref: string) => void;
}

export const HoldRow = memo(function HoldRow({ hold, simT, selected, pendingLabel, banks, onSelect }: Props) {
  const d = deadlineInfo(hold, simT);
  const open = isOpen(hold);
  const top = [...hold.reasons].sort((a, b) => b.weight - a.weight).slice(0, 3);
  return (
    <li style={{ height: ROW_HEIGHT }} className="px-2 py-1">
      <button
        type="button"
        onClick={() => onSelect(hold.txn_ref)}
        aria-pressed={selected}
        data-testid="hold-row"
        data-ref={hold.txn_ref}
        className={cn(
          "flex h-full w-full items-center gap-3 rounded-xl border px-3 text-left transition-colors",
          selected ? "border-intel/60 bg-intel-soft" : "border-line bg-chip hover:bg-hover",
          d.overdue && !selected && "border-danger/50",
        )}
      >
        <CountdownRing
          remaining={d.remaining}
          total={d.total}
          state={open ? (d.overdue ? "overdue" : "open") : "resolved"}
          size={48}
        />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="mono tnum text-sm font-semibold">{formatInr(hold.amount_inr)}</span>
            <span className="rounded bg-raised px-1 text-[10px] font-semibold text-muted">{hold.rail}</span>
            <span className="text-[11px] text-muted">{bankLabel(hold.bank, banks)}</span>
            <DecisionBadge value={open ? hold.decision : hold.state} />
            {pendingLabel && <span className="text-[10.5px] font-medium text-intel">{pendingLabel}</span>}
          </div>
          <ScoreMeter score={hold.score} />
          <div className="flex items-center gap-1.5 truncate text-[10.5px] text-muted">
            {d.overdue && (
              <span className="inline-flex shrink-0 items-center gap-1 font-semibold text-danger-text">
                <AlertTriangle className="h-3 w-3" aria-hidden /> {overdueBy(d.remaining)}
              </span>
            )}
            <span className="truncate">{top.map((r) => reasonLabel(r.code)).join(" · ") || "No reasons"}</span>
          </div>
        </div>
      </button>
    </li>
  );
});
