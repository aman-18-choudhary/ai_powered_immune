import { AnimatePresence, motion } from "framer-motion";
import { Link2 } from "lucide-react";
import type { LedgerRecord } from "@/state/reducer";
import { useMotionPrefs } from "@/lib/motion/prefs";

/** Hash-chain strip: the newest entries as linked blocks; a new block slides in and its link draws. */
export function LedgerChain({ entries, max = 4 }: { entries: LedgerRecord[]; max?: number }) {
  const { reduced } = useMotionPrefs();
  const shown = entries.slice(-max);
  if (shown.length === 0) {
    return <p className="px-1 text-[11px] text-subtle">Ledger entries appear here as decisions are recorded.</p>;
  }
  return (
    <ol className="flex items-stretch gap-0 overflow-hidden" aria-label="Evidence ledger, newest entries">
      <AnimatePresence initial={false}>
        {shown.map((e, i) => (
          <motion.li
            key={e.seq}
            layout={!reduced}
            initial={reduced ? false : { opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0 }}
            transition={{ type: "spring", stiffness: 260, damping: 26 }}
            className="flex min-w-0 items-center"
          >
            {i > 0 && (
              <span className="relative flex h-px w-3 items-center" aria-hidden>
                <motion.span
                  className="absolute inset-0 origin-left bg-intel"
                  initial={reduced ? false : { scaleX: 0 }}
                  animate={{ scaleX: 1 }}
                  transition={{ delay: 0.15, duration: 0.3 }}
                />
              </span>
            )}
            <div className="min-w-0 rounded-lg border border-intel/40 bg-intel-soft px-2 py-1">
              <div className="flex items-center gap-1 text-[10px] font-semibold text-intel">
                <Link2 className="h-3 w-3" aria-hidden />
                <span className="mono tnum">#{e.seq}</span>
              </div>
              <div className="truncate text-[10.5px] text-muted" title={e.event_type}>
                {e.event_type}
              </div>
              <div className="mono truncate text-[9.5px] text-subtle">{e.hash.slice(0, 8)}</div>
            </div>
          </motion.li>
        ))}
      </AnimatePresence>
    </ol>
  );
}
