import { motion } from "framer-motion";
import { BookOpenCheck, Hourglass, IndianRupee, Lock, PhoneCall, Sparkles, type LucideIcon } from "lucide-react";
import { AnimatedNumber } from "@/lib/motion/AnimatedNumber";
import { formatCount, formatInr, formatLatencyMs, formatSeconds, groupIndian } from "@/lib/format";
import { listContainer, listItem } from "@/lib/motion/variants";
import { cn } from "@/lib/utils/cn";

export interface KpiValues {
  callsFlagged: number;
  transfersHeld: number;
  protectedRupees: number | null;
  protectedExact: string | null;
  antibodiesActive: number;
  medianTimeToBlockS: number | null;
  antibodyLatencyMs: number | null;
  ledgerEntries: number;
}

interface Tile {
  key: string;
  label: string;
  icon: LucideIcon;
  tone: string;
  value: React.ReactNode;
  sub?: string;
  title?: string;
}

export function KpiStrip({ v, loadingMoney }: { v: KpiValues; loadingMoney?: boolean }) {
  const tiles: Tile[] = [
    {
      key: "calls", label: "Calls flagged", icon: PhoneCall, tone: "text-danger-text",
      value: <AnimatedNumber value={v.callsFlagged} format={formatCount} />,
      sub: "scam calls scored over threshold",
    },
    {
      key: "held", label: "Transfers held", icon: Lock, tone: "text-warn",
      value: <AnimatedNumber value={v.transfersHeld} format={formatCount} />,
      sub: "held for verification",
    },
    {
      key: "protected", label: "Held or blocked", icon: IndianRupee, tone: "text-safe",
      value:
        v.protectedRupees === null ? (
          <span className="text-subtle">{loadingMoney ? "…" : "—"}</span>
        ) : (
          <AnimatedNumber value={v.protectedRupees} format={(n) => "₹" + groupIndian(Math.round(n).toString())} />
        ),
      sub: v.protectedExact ? `exact ${formatInr(v.protectedExact)}` : "needs officer view",
      title: "Sum of exact amounts of transfers currently held or blocked (officer/analyst REST view)",
    },
    {
      key: "ab", label: "Antibodies active", icon: Sparkles, tone: "text-intel",
      value: <AnimatedNumber value={v.antibodiesActive} format={formatCount} />,
      sub: v.antibodyLatencyMs != null ? `applied in ${formatLatencyMs(v.antibodyLatencyMs)}` : "none shared yet",
    },
    {
      key: "ttb", label: "Median time to block", icon: Hourglass, tone: "text-flow",
      value:
        v.medianTimeToBlockS === null ? (
          <span className="text-subtle">—</span>
        ) : (
          <span>{formatSeconds(v.medianTimeToBlockS)}</span>
        ),
      sub: "hold to block, sim clock",
    },
    {
      key: "ledger", label: "Ledger entries", icon: BookOpenCheck, tone: "text-intel",
      value: <AnimatedNumber value={v.ledgerEntries} format={formatCount} />,
      sub: "hash-chained, signed",
    },
  ];
  return (
    <motion.ul variants={listContainer} initial="hidden" animate="show" className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6" aria-label="Key figures">
      {tiles.map((t) => (
        <motion.li key={t.key} variants={listItem} className="panel panel-blur p-3.5" title={t.title}>
          <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted">
            <t.icon className={cn("h-3.5 w-3.5", t.tone)} aria-hidden />
            {t.label}
          </div>
          <div className="mono tnum mt-1.5 text-[26px] font-semibold leading-none tracking-tight" data-testid={`kpi-${t.key}`}>
            {t.value}
          </div>
          {t.sub && <div className="mt-1.5 truncate text-[11px] text-subtle">{t.sub}</div>}
        </motion.li>
      ))}
    </motion.ul>
  );
}
