import { reasonLabel } from "@/lib/format";
import type { Reason } from "@/types/api";
import { cn } from "@/lib/utils/cn";

export function ReasonChips({ reasons, max = 4, highlight }: { reasons: Reason[]; max?: number; highlight?: string }) {
  const shown = [...reasons].sort((a, b) => b.weight - a.weight).slice(0, max);
  if (shown.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1" aria-label="Reasons">
      {shown.map((r) => (
        <li
          key={r.code}
          title={r.detail}
          className={cn(
            "mono rounded-md border px-1.5 py-0.5 text-[10.5px] leading-4",
            r.code === highlight
              ? "border-intel/50 bg-intel-soft text-intel"
              : "border-line bg-chip text-muted",
          )}
        >
          {r.code === "ANTIBODY_MATCH" ? "ANTIBODY_MATCH" : reasonLabel(r.code)}
        </li>
      ))}
    </ul>
  );
}
