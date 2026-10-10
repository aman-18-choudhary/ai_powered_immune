import { ArrowRight, Check, ShieldAlert, ShieldCheck, StickyNote, Undo2 } from "lucide-react";
import { CountdownRing } from "@/components/common/CountdownRing";
import { DecisionBadge, ScoreMeter } from "@/components/common/Decision";
import { RefChip } from "@/components/common/RefChip";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { bankLabel, formatBucket, formatInr, formatSimClock } from "@/lib/format";
import { reasonInfo } from "@/lib/reasons";
import { deadlineInfo, historyChain, isOpen, overdueBy, TRIGGER_LABEL, type ResolveAction } from "@/lib/analyst/queue";
import { buildHoldTimeline, type TimelineItem } from "@/lib/analyst/timeline";
import type { RunState } from "@/state/reducer";
import type { HoldView } from "@/types/api";
import { cn } from "@/lib/utils/cn";

interface Props {
  hold: HoldView;
  run: RunState;
  simT: number;
  canAct: boolean;
  pending?: ResolveAction;
  note?: { note: string; action: ResolveAction };
  onAction: (a: ResolveAction) => void;
}

const DOT: Record<TimelineItem["kind"], string> = {
  call: "bg-danger", scored: "bg-flow", decision: "bg-warn", antibody: "bg-intel", ledger: "bg-subtle", resolved: "bg-safe",
};

export function HoldDetail({ hold, run, simT, canAct, pending, note, onAction }: Props) {
  const d = deadlineInfo(hold, simT);
  const open = isOpen(hold) && !pending;
  const rec = run.holds[hold.txn_ref];
  const chain = historyChain(rec?.history ?? [], hold.decision, rec?.upgradedFrom);
  const reasons = [...hold.reasons].sort((a, b) => b.weight - a.weight);
  const items = buildHoldTimeline(run, hold);
  return (
    <div className="space-y-5 p-4" data-testid="hold-detail">
      <div className="flex flex-wrap items-start gap-4">
        <CountdownRing remaining={d.remaining} total={d.total} state={isOpen(hold) ? (d.overdue ? "overdue" : "open") : "resolved"} size={64} />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="mono tnum text-2xl font-semibold" data-testid="detail-amount">
              {formatInr(hold.amount_inr)}
            </span>
            <span className="rounded bg-chip px-1.5 py-0.5 text-xs font-semibold text-muted">{hold.rail}</span>
            <span className="text-sm text-muted">{bankLabel(hold.bank, run.banks)}</span>
          </div>
          <p className="text-xs text-subtle">
            Amount band on the stream: {formatBucket(hold.amount_bucket)}. Exact value shown to analyst and officer roles only.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <DecisionBadge value={isOpen(hold) ? hold.decision : hold.state} />
            {d.overdue && <span className="text-xs font-semibold text-danger-text">{overdueBy(d.remaining)}: still open, needs a human decision</span>}
          </div>
        </div>
      </div>

      <ScoreMeter score={hold.score} />

      <div className="flex flex-wrap gap-2 text-xs">
        {hold.payee_ref && (
          <span className="inline-flex items-center gap-1.5 text-muted">
            payee <RefChip value={hold.payee_ref} n={10} />
          </span>
        )}
        {hold.call_ref && (
          <span className="inline-flex items-center gap-1.5 text-muted">
            call <RefChip value={hold.call_ref} n={14} />
          </span>
        )}
        <span className="inline-flex items-center gap-1.5 text-muted">
          txn <RefChip value={hold.txn_ref} n={10} />
        </span>
      </div>

      <section aria-label="Reasons">
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">Why it was held</h3>
        <ul className="space-y-2">
          {reasons.map((r) => {
            const info = reasonInfo(r.code);
            return (
              <li key={r.code} className="rounded-xl border border-line bg-chip px-3 py-2" data-testid="reason-row">
                <div className="flex flex-wrap items-center gap-2">
                  <Tooltip content={r.code}>
                    <span className="text-sm font-medium">{info.label}</span>
                  </Tooltip>
                  <span className="mono tnum text-[10.5px] text-subtle">weight {r.weight.toFixed(2)}</span>
                </div>
                <p className="text-xs text-muted">{info.meaning}</p>
                {r.detail && <p className="mt-0.5 text-[11px] text-subtle">{r.detail}</p>}
              </li>
            );
          })}
          {reasons.length === 0 && <li className="text-xs text-subtle">The service attached no reasons to this decision.</li>}
        </ul>
      </section>

      <section aria-label="Decision history">
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">Decision history</h3>
        <p className="flex flex-wrap items-center gap-1.5" data-testid="history-chain">
          {chain.map((c, i) => (
            <span key={`${c}-${i}`} className="inline-flex items-center gap-1.5">
              {i > 0 && <ArrowRight className="h-3.5 w-3.5 text-subtle" aria-label="then" />}
              <span className="mono rounded-md border border-line bg-chip px-1.5 py-0.5 text-xs">{c}</span>
            </span>
          ))}
          <span className="text-xs text-subtle">decision_seq {hold.decision_seq}</span>
        </p>
        {(rec?.history.length ?? 0) > 0 && (
          <ul className="mt-2 space-y-1 text-xs text-muted">
            {rec?.history.map((s) => (
              <li key={`${s.seq}-${s.decision}`}>
                <span className="mono tnum text-subtle">T+{formatSimClock(s.t)}</span> · seq {s.seq}: {s.decision}
                {s.from ? ` (from ${s.from})` : ""} · trigger: {TRIGGER_LABEL[s.trigger]}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Resolve" className="space-y-2">
        {canAct ? (
          <div className="flex flex-wrap gap-2">
            <Button variant="danger" disabled={!open} onClick={() => onAction("confirm_block")} data-testid="act-confirm">
              <ShieldAlert className="h-4 w-4" aria-hidden /> Confirm mule <kbd className="mono ml-1 rounded border border-current/30 px-1 text-[10px]">C</kbd>
            </Button>
            <Button variant="safe" disabled={!open} onClick={() => onAction("release")} data-testid="act-release">
              <Undo2 className="h-4 w-4" aria-hidden /> Release <kbd className="mono ml-1 rounded border border-current/30 px-1 text-[10px]">R</kbd>
            </Button>
          </div>
        ) : (
          <p className="text-xs text-muted">Read-only role: you can inspect holds but not resolve them.</p>
        )}
        {pending && (
          <p className="flex items-center gap-1.5 text-xs text-intel" role="status">
            <Check className="h-3.5 w-3.5" aria-hidden /> Sending {pending === "confirm_block" ? "confirm mule" : "release"}…
          </p>
        )}
        {!isOpen(hold) && (
          <p className="flex items-center gap-1.5 text-xs text-muted">
            <ShieldCheck className="h-3.5 w-3.5 text-safe" aria-hidden />
            Resolved as <strong className="text-fg">{hold.state}</strong>
            {hold.resolved_by ? ` by ${hold.resolved_by}` : ""}.
          </p>
        )}
        {note && (
          <p className="flex items-start gap-1.5 rounded-lg border border-line bg-chip px-2.5 py-1.5 text-xs text-muted" data-testid="local-note">
            <StickyNote className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            <span>
              <strong className="text-fg">Local audit note</strong> (this browser only, not sent): {note.note}
            </span>
          </p>
        )}
      </section>

      <section aria-label="Hold timeline">
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">Timeline</h3>
        <ol className="relative space-y-3 border-l border-line pl-4">
          {items.map((i) => (
            <li key={i.key} className="relative">
              <span className={cn("absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-bg", DOT[i.kind])} aria-hidden />
              <div className="flex items-baseline gap-2">
                <span className="mono tnum text-[11px] text-subtle">T+{formatSimClock(i.t)}</span>
                <span className="text-[13px] font-medium">{i.title}</span>
              </div>
              {i.detail && <p className="text-xs text-muted">{i.detail}</p>}
            </li>
          ))}
        </ol>
        <p className="mt-2 text-[11px] text-subtle">Ledger rows are matched by simulated time window: the stream carries no per-entry transaction reference.</p>
      </section>
    </div>
  );
}
