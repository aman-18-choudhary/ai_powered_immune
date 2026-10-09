import { memo, useMemo, useState } from "react";
import { Activity } from "lucide-react";
import { Panel, EmptyState } from "@/components/common/Panel";
import { SeverityIcon } from "@/components/common/Severity";
import { RefChip } from "@/components/common/RefChip";
import { useVirtualList } from "@/lib/hooks/useVirtualList";
import { formatSimClock, bankLabel } from "@/lib/format";
import type { IncidentRecord, Severity } from "@/state/reducer";
import { cn } from "@/lib/utils/cn";

const ROW = 60;
const VIEW = 300;
const FILTERS: { key: "all" | Severity; label: string }[] = [
  { key: "all", label: "All" },
  { key: "critical", label: "Critical" },
  { key: "warn", label: "Verify" },
  { key: "safe", label: "Blocked" },
  { key: "intel", label: "Intel" },
];

const Row = memo(function Row({ i, banks }: { i: IncidentRecord; banks: { bank_a: string; bank_b: string } | null }) {
  return (
    <li className="flex h-[60px] items-center gap-3 border-b border-line px-3 text-[12.5px]">
      <SeverityIcon severity={i.severity} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-medium">{i.title}</span>
          {i.bank && <span className="rounded bg-chip px-1 text-[10px] font-semibold text-muted">{bankLabel(i.bank, banks)}</span>}
        </div>
        <div className="truncate text-[11.5px] text-muted">{i.detail}</div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1">
        <span className="mono tnum text-[11px] text-subtle">T+{formatSimClock(i.t)}</span>
        {i.ref && <RefChip value={i.ref} n={6} />}
      </div>
    </li>
  );
});

export function IncidentFeed({ incidents, banks, height = VIEW }: { incidents: IncidentRecord[]; banks: { bank_a: string; bank_b: string } | null; height?: number }) {
  const [filter, setFilter] = useState<"all" | Severity>("all");
  const list = useMemo(() => {
    const f = filter === "all" ? incidents : incidents.filter((i) => i.severity === filter);
    return [...f].reverse();
  }, [incidents, filter]);
  const v = useVirtualList(list.length, ROW, height);
  const slice = list.slice(v.start, v.end);

  return (
    <Panel
      title="Incident feed"
      icon={<Activity className="h-3.5 w-3.5" aria-hidden />}
      className="h-full"
      actions={
        <div role="group" aria-label="Filter incidents" className="flex gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              aria-pressed={filter === f.key}
              onClick={() => setFilter(f.key)}
              className={cn(
                "rounded-md px-1.5 py-0.5 text-[10.5px] font-medium transition-colors",
                filter === f.key ? "bg-raised text-fg" : "text-muted hover:text-fg",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      }
      bodyClassName="relative"
    >
      {list.length === 0 ? (
        <EmptyState icon={<Activity className="h-5 w-5" aria-hidden />} title="Waiting for events…" hint="Incidents appear as the scenario runs." />
      ) : (
        <div className="h-full overflow-y-auto" style={{ maxHeight: height }} onScroll={v.onScroll} tabIndex={0} aria-label="Incident list">
          <ul style={{ paddingTop: v.padTop, paddingBottom: v.padBottom }}>
            {slice.map((i) => (
              <Row key={i.id} i={i} banks={banks} />
            ))}
          </ul>
        </div>
      )}
      <span className="sr-only" aria-live="polite">
        {incidents.length > 0 ? `${incidents[incidents.length - 1]?.title ?? ""}` : ""}
      </span>
    </Panel>
  );
}
