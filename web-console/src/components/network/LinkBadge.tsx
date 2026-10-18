import { Ban, BadgeCheck, Link2, Waypoints, type LucideIcon } from "lucide-react";
import { LINK_HINT, LINK_LABEL, type LinkType } from "@/lib/complaints/network";
import { cn } from "@/lib/utils/cn";

const STYLE: Record<LinkType, { Icon: LucideIcon; cls: string }> = {
  observed: { Icon: Link2, cls: "text-fg bg-flow-soft border-flow/40 [&>svg]:text-flow" },
  inferred: { Icon: Waypoints, cls: "text-warn bg-warn-soft border-warn/40 border-dashed" },
  confirmed: { Icon: BadgeCheck, cls: "text-safe bg-safe-soft border-safe/40" },
  rejected: { Icon: Ban, cls: "text-muted bg-chip border-line" },
};

/** Link type as icon + text (never colour alone); the hint explains what the type means. */
export function LinkBadge({ type, className }: { type: LinkType; className?: string }) {
  const { Icon, cls } = STYLE[type];
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium", cls, className)} title={LINK_HINT[type]} data-testid="link-badge" data-link={type}>
      <Icon className="h-3 w-3" aria-hidden />
      {LINK_LABEL[type]}
    </span>
  );
}
