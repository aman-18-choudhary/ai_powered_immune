import { motion } from "framer-motion";
import { Sparkles } from "lucide-react";
import { Panel, EmptyState } from "@/components/common/Panel";
import { RefChip } from "@/components/common/RefChip";
import { bankLabel, formatLatencyMs, formatSimClock } from "@/lib/format";
import { useMotionPrefs } from "@/lib/motion/prefs";
import type { AntibodyRecord } from "@/state/reducer";

function Ripple({ from, to, latency, banks }: { from: string; to: string; latency: number | null; banks: { bank_a: string; bank_b: string } | null }) {
  const { reduced } = useMotionPrefs();
  return (
    <div className="flex items-center gap-2" aria-label={`${bankLabel(from, banks)} to ${bankLabel(to, banks)}`}>
      <span className="grid h-8 min-w-[52px] place-items-center rounded-lg border border-intel/50 bg-intel-soft px-1.5 text-[11px] font-semibold text-intel">
        {bankLabel(from, banks)}
      </span>
      <div className="relative h-8 flex-1">
        <svg className="absolute inset-0 h-full w-full overflow-visible" viewBox="0 0 100 32" preserveAspectRatio="none" aria-hidden>
          <line x1="0" y1="16" x2="100" y2="16" stroke="var(--intel)" strokeOpacity="0.4" strokeDasharray="3 4" vectorEffect="non-scaling-stroke" />
        </svg>
        {!reduced && latency !== null && (
          <>
            <motion.span
              className="absolute top-1/2 h-2.5 w-2.5 -translate-y-1/2 rounded-full bg-intel shadow-[0_0_12px_var(--intel)]"
              initial={{ left: "0%", opacity: 1 }}
              animate={{ left: "calc(100% - 10px)", opacity: [1, 1, 0.3] }}
              transition={{ duration: 1.4, ease: "easeInOut", repeat: Infinity, repeatDelay: 1.2 }}
            />
            <motion.span
              className="absolute right-0 top-1/2 h-6 w-6 -translate-y-1/2 rounded-full border border-intel"
              initial={{ scale: 0.3, opacity: 0 }}
              animate={{ scale: [0.3, 1.6], opacity: [0.9, 0] }}
              transition={{ duration: 1.2, ease: "easeOut", repeat: Infinity, repeatDelay: 1.4, delay: 1.2 }}
            />
          </>
        )}
        <span className="mono tnum absolute left-1/2 top-0 -translate-x-1/2 rounded bg-solid px-1 text-[10px] text-fg">
          {latency !== null ? formatLatencyMs(latency) : "pending"}
        </span>
      </div>
      <span
        className={
          latency !== null
            ? "grid h-8 min-w-[52px] place-items-center rounded-lg border border-intel/50 bg-intel-soft px-1.5 text-[11px] font-semibold text-intel"
            : "grid h-8 min-w-[52px] place-items-center rounded-lg border border-line px-1.5 text-[11px] text-subtle"
        }
      >
        {bankLabel(to, banks)}
      </span>
    </div>
  );
}

export function AntibodyTicker({ antibodies, banks, otherBank }: { antibodies: AntibodyRecord[]; banks: { bank_a: string; bank_b: string } | null; otherBank: string }) {
  const list = [...antibodies].reverse();
  return (
    <Panel title="Antibody ticker" icon={<Sparkles className="h-3.5 w-3.5 text-intel" aria-hidden />} className="h-full" bodyClassName="overflow-y-auto p-3">
      {list.length === 0 ? (
        <EmptyState icon={<Sparkles className="h-5 w-5" aria-hidden />} title="No antibodies yet" hint="A confirmed mule becomes an antibody and ripples to the other banks." />
      ) : (
        <ul className="space-y-3">
          {list.map((a) => {
            const applied = a.applied[0];
            return (
              <li key={a.ref} className="rounded-xl border border-line bg-chip p-3">
                <div className="mb-2 flex items-center gap-2 text-[12px]">
                  <span className="font-semibold text-intel">{a.kind.replace(/_/g, " ")}</span>
                  <RefChip value={a.ref} />
                  <span className="mono tnum ml-auto text-[11px] text-subtle">T+{formatSimClock(a.publishedT)}</span>
                </div>
                <Ripple from={a.bank || "bank_a"} to={applied?.bank ?? otherBank} latency={applied ? applied.latency_ms : null} banks={banks} />
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
