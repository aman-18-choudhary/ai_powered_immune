import { AlertOctagon, AlertTriangle, CheckCircle2, Info, Sparkles } from "lucide-react";
import type { Severity } from "@/state/reducer";
import { cn } from "@/lib/utils/cn";

const MAP = {
  critical: { Icon: AlertOctagon, label: "Critical", cls: "text-danger-text bg-danger-soft border-danger/40" },
  warn: { Icon: AlertTriangle, label: "Verify", cls: "text-warn bg-warn-soft border-warn/40" },
  safe: { Icon: CheckCircle2, label: "Blocked", cls: "text-safe bg-safe-soft border-safe/40" },
  intel: { Icon: Sparkles, label: "Intel", cls: "text-intel bg-intel-soft border-intel/40" },
  info: { Icon: Info, label: "Info", cls: "text-muted bg-chip border-line" },
} as const;

/** Risk is never colour alone: icon + text label. */
export function SeverityBadge({ severity, label, className }: { severity: Severity; label?: string; className?: string }) {
  const { Icon, label: l, cls } = MAP[severity];
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium", cls, className)}>
      <Icon className="h-3 w-3" aria-hidden />
      {label ?? l}
    </span>
  );
}

export function SeverityIcon({ severity, className }: { severity: Severity; className?: string }) {
  const { Icon, cls } = MAP[severity];
  return <Icon className={cn("h-4 w-4", cls.split(" ")[0], className)} aria-hidden />;
}
