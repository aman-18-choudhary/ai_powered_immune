import { useId, type ReactNode } from "react";
import { AlertOctagon, AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, CircleDot, Clock, FileSearch, Gauge, Loader2, RefreshCw, Search, ServerOff, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/common/Panel";
import { resultText, STATUS_LABEL } from "@/lib/complaints/format";
import type { Assessment, ComplaintStatus, RiskLevel } from "@/lib/complaints/types";
import { cn } from "@/lib/utils/cn";

const STATUS_STYLE: Record<ComplaintStatus, { Icon: typeof Clock; cls: string }> = {
  // body-colour text: cyan text on the cyan tint is below 4.5:1 in the light theme
  SUBMITTED: { Icon: CircleDot, cls: "text-fg bg-flow-soft border-flow/40 [&>svg]:text-flow" },
  UNDER_REVIEW: { Icon: FileSearch, cls: "text-intel bg-intel-soft border-intel/40" },
  INVESTIGATING: { Icon: Search, cls: "text-warn bg-warn-soft border-warn/40" },
  RESOLVED: { Icon: CheckCircle2, cls: "text-safe bg-safe-soft border-safe/40" },
  REJECTED: { Icon: XCircle, cls: "text-muted bg-chip border-line" },
};

/** Status as icon + text (never colour alone). */
export function StatusChip({ status, className }: { status: ComplaintStatus; className?: string }) {
  const { Icon, cls } = STATUS_STYLE[status] ?? STATUS_STYLE.SUBMITTED;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium", cls, className)} data-testid="status-chip">
      <Icon className="h-3 w-3" aria-hidden />
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

const RISK_STYLE: Record<RiskLevel, { Icon: typeof Clock; cls: string }> = {
  LOW: { Icon: CheckCircle2, cls: "text-safe bg-safe-soft border-safe/40" },
  MEDIUM: { Icon: AlertTriangle, cls: "text-warn bg-warn-soft border-warn/40" },
  HIGH: { Icon: AlertOctagon, cls: "text-danger-text bg-danger-soft border-danger/40" },
  CRITICAL: { Icon: AlertOctagon, cls: "text-danger-text bg-danger-soft border-danger/60" },
};

/**
 * Analysis result as icon + words (+ score). Evidence wording, never a verdict: LOW and "no
 * indicators found" are neutral (no green tick), because finding no cue is not a finding of safety.
 * The score is an uncalibrated heuristic of indicator strength, not a probability.
 */
export function RiskBadge({
  level,
  score,
  assessment,
  className,
}: {
  level: RiskLevel | null;
  score?: number | null;
  assessment?: Assessment | null;
  className?: string;
}) {
  const neutral = !level || level === "LOW" || assessment === "no_indicators_found" || assessment === "insufficient_evidence" || assessment === "not_assessed";
  const text = resultText(level, assessment === "no_indicators_found" ? null : score, assessment);
  if (neutral) {
    return (
      <span className={cn("inline-flex items-center gap-1 rounded-md border border-line bg-chip px-1.5 py-0.5 text-[11px] font-medium text-muted", className)} data-testid="risk-badge">
        <Gauge className="h-3 w-3" aria-hidden /> {level ? text : assessment === "insufficient_evidence" ? text : "Risk not scored"}
      </span>
    );
  }
  const { Icon, cls } = RISK_STYLE[level];
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium", cls, className)} data-testid="risk-badge">
      <Icon className="h-3 w-3" aria-hidden />
      {text}
    </span>
  );
}

export function LoadingBlock({ label = "Loading" }: { label?: string }) {
  return (
    <div className="space-y-3 p-4" aria-busy="true" role="status" aria-label={label}>
      <Skeleton className="h-5 w-48" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
    </div>
  );
}

/** An error or unavailable state with a retry. `unavailable` is for a service that is down (503 / network). */
export function ErrorBlock({ message, onRetry, unavailable }: { message: string; onRetry?: () => void; unavailable?: boolean }) {
  return (
    <div role="alert" className="m-4 flex flex-col items-start gap-2 rounded-xl border border-danger/40 bg-danger-soft p-4 text-sm" data-testid="error-block">
      <p className="flex items-center gap-2 font-medium">
        {unavailable ? <ServerOff className="h-4 w-4 shrink-0" aria-hidden /> : <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />}
        {unavailable ? "Service unavailable" : "Could not load"}
      </p>
      <p className="text-muted">{message}</p>
      {onRetry && (
        <Button size="sm" variant="secondary" onClick={onRetry}>
          <RefreshCw className="h-3.5 w-3.5" aria-hidden /> Try again
        </Button>
      )}
    </div>
  );
}

export function Pager({ total, limit, offset, onChange, label }: { total: number; limit: number; offset: number; onChange: (offset: number) => void; label: string }) {
  if (total <= limit && offset === 0) return null;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(total, offset + limit);
  return (
    <nav aria-label={`${label} pages`} className="flex items-center justify-between gap-3 border-t border-line px-4 py-2.5 text-xs text-muted">
      <span className="tnum" aria-live="polite">
        {from}–{to} of {total}
      </span>
      <div className="flex gap-2">
        <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))} aria-label="Previous page">
          <ChevronLeft className="h-4 w-4" aria-hidden /> Previous
        </Button>
        <Button size="sm" variant="secondary" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)} aria-label="Next page">
          Next <ChevronRight className="h-4 w-4" aria-hidden />
        </Button>
      </div>
    </nav>
  );
}

export const inputCls =
  "w-full rounded-xl border border-line-strong bg-sunken px-3 py-2 text-[15px] outline-none focus-visible:border-intel aria-[invalid=true]:border-danger disabled:opacity-60";

/** Label + control + hint + error, wired with aria-describedby / aria-invalid. */
export function Field({
  label,
  optional,
  hint,
  error,
  children,
  className,
}: {
  label: string;
  optional?: boolean;
  hint?: string;
  error?: string;
  children: (p: { id: string; "aria-describedby"?: string; "aria-invalid"?: boolean }) => ReactNode;
  className?: string;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errId = error ? `${id}-err` : undefined;
  const describedBy = [hintId, errId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("space-y-1", className)}>
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
        {optional ? <span className="ml-1.5 text-xs font-normal text-subtle">(optional)</span> : <span className="ml-1 text-danger-text" aria-hidden>*</span>}
        {!optional && <span className="sr-only"> (required)</span>}
      </label>
      {children({ id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined })}
      {hint && (
        <p id={hintId} className="text-xs text-subtle">
          {hint}
        </p>
      )}
      {error && (
        <p id={errId} className="text-xs font-medium text-danger-text" data-testid="field-error">
          {error}
        </p>
      )}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-4 w-4 animate-spin", className)} aria-hidden />;
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}
