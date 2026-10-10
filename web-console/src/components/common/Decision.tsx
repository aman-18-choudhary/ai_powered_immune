import { AlertOctagon, CheckCircle2, ShieldAlert, ShieldCheck, Undo2 } from "lucide-react";
import { cn } from "@/lib/utils/cn";

interface Look {
  label: string;
  cls: string;
  Icon: typeof AlertOctagon;
}

const LOOKS: Record<string, Look> = {
  allow: { label: "Allow", cls: "border-line bg-chip text-muted", Icon: CheckCircle2 },
  step_up: { label: "Step-up", cls: "border-warn/40 bg-warn-soft text-warn", Icon: ShieldAlert },
  hold_verify: { label: "Hold and verify", cls: "border-warn/40 bg-warn-soft text-warn", Icon: ShieldAlert },
  hold: { label: "Hold", cls: "border-warn/40 bg-warn-soft text-warn", Icon: ShieldAlert },
  block: { label: "Block", cls: "border-danger/40 bg-danger-soft text-danger-text", Icon: AlertOctagon },
  blocked: { label: "Blocked", cls: "border-safe/40 bg-safe-soft text-safe", Icon: ShieldCheck },
  released: { label: "Released", cls: "border-line-strong bg-chip text-fg", Icon: Undo2 },
};

export function decisionLabel(d: string): string {
  return LOOKS[d]?.label ?? d.replace(/_/g, " ");
}

/** Decision or state as icon + text (never colour alone). */
export function DecisionBadge({ value, className }: { value: string; className?: string }) {
  const l = LOOKS[value] ?? { label: value.replace(/_/g, " "), cls: "border-line bg-chip text-muted", Icon: CheckCircle2 };
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium", l.cls, className)}>
      <l.Icon className="h-3 w-3" aria-hidden />
      {l.label}
    </span>
  );
}

/** Horizontal 0..1 score meter with a numeric readout. */
export function ScoreMeter({ score, label = "Risk score", className }: { score: number; label?: string; className?: string }) {
  const v = Math.min(1, Math.max(0, score));
  const tone = v >= 0.7 ? "var(--danger)" : v >= 0.35 ? "var(--warn)" : "var(--safe)";
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={Number(v.toFixed(2))}
        className="h-1.5 flex-1 overflow-hidden rounded-full bg-chip"
      >
        <div className="h-full rounded-full transition-[width] duration-300" style={{ width: `${v * 100}%`, background: tone }} />
      </div>
      <span className="mono tnum w-9 text-right text-[11px] text-muted">{v.toFixed(2)}</span>
    </div>
  );
}
