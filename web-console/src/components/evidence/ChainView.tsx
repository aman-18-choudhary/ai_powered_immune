import { useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { EyeOff, Flag, Link2 } from "lucide-react";
import type { Checkpoint, LedgerEntry } from "@/lib/evidence/ledger";
import { cn } from "@/lib/utils/cn";

interface Props {
  entries: LedgerEntry[];
  checkpoints: Checkpoint[];
  /** Sequence numbers whose payload the exported package withholds (hash only). */
  redacted: Set<number>;
  /** Entries that arrived after first paint animate in. */
  freshSeqs: Set<number>;
  selected: number | null;
  onInspect: (seq: number) => void;
}

const pre = (h: string, n = 8) => h.slice(0, n);

/** Entries as linked blocks. Each connector shows how a block's prev_hash binds the one before. */
export function ChainView({ entries, checkpoints, redacted, freshSeqs, selected, onInspect }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const lastSeq = entries[entries.length - 1]?.seq ?? 0;
  useEffect(() => {
    const el = scroller.current;
    if (el && typeof el.scrollTo === "function") el.scrollTo({ left: el.scrollWidth, behavior: "smooth" });
  }, [lastSeq]);
  const cpBySeq = new Map(checkpoints.map((c) => [c.seq, c]));
  return (
    <div
      ref={scroller}
      className="overflow-x-auto pb-3 pt-1"
      tabIndex={0}
      aria-label="Hash chain: ledger entries as linked blocks, scroll horizontally"
      data-testid="chain"
    >
      <ol className="flex min-w-max items-stretch px-2">
        {entries.map((e, i) => {
          const sealed = redacted.has(e.seq) || !e.payload_present;
          const cp = cpBySeq.get(e.seq);
          const fresh = freshSeqs.has(e.seq);
          const prev = entries[i - 1];
          const linked = !prev || prev.entry_hash === e.prev_hash || i === 0;
          return (
            <motion.li
              key={e.seq}
              initial={fresh ? { opacity: 0, x: 48 } : false}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
              className="flex items-stretch"
              data-testid="chain-block"
              data-seq={e.seq}
            >
              {i > 0 && (
                <div className="relative flex w-14 shrink-0 flex-col items-center justify-center" aria-hidden>
                  <svg width="56" height="24" viewBox="0 0 56 24" className="overflow-visible">
                    <motion.path
                      d="M0 12 H56"
                      stroke={linked ? "var(--flow)" : "var(--danger)"}
                      strokeWidth="2"
                      strokeDasharray={linked ? "0" : "4 4"}
                      initial={fresh ? { pathLength: 0 } : false}
                      animate={{ pathLength: 1 }}
                      transition={{ duration: 0.5, delay: fresh ? 0.25 : 0 }}
                    />
                    <path d="M50 7 L56 12 L50 17" fill="none" stroke={linked ? "var(--flow)" : "var(--danger)"} strokeWidth="2" />
                  </svg>
                  <span className="mono mt-0.5 text-[9px] text-subtle">{pre(e.prev_hash, 6)}</span>
                </div>
              )}
              <div className="flex flex-col items-center">
                <div className="mb-1 h-6">
                  {cp && (
                    <span
                      className="inline-flex items-center gap-1 rounded-md border border-intel/50 bg-intel-soft px-1.5 py-0.5 text-[10px] font-semibold text-intel"
                      title={`Signed checkpoint ${cp.checkpoint_id} (key ${cp.key_id})`}
                    >
                      <Flag className="h-3 w-3" aria-hidden /> signed checkpoint
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => onInspect(e.seq)}
                  aria-label={`Inspect ledger entry ${e.seq}, ${e.event_type}${sealed ? ", payload withheld" : ""}`}
                  aria-pressed={selected === e.seq}
                  className={cn(
                    "relative w-[176px] rounded-xl border p-3 text-left transition-colors",
                    sealed
                      ? "border-dashed border-line-strong bg-[repeating-linear-gradient(135deg,transparent_0_7px,var(--border)_7px_8px)] hover:bg-hover"
                      : "border-line bg-chip hover:bg-hover",
                    selected === e.seq && "border-intel ring-1 ring-intel",
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span className="mono tnum text-sm font-semibold">#{e.seq}</span>
                    {sealed ? <EyeOff className="h-3.5 w-3.5 text-muted" aria-hidden /> : <Link2 className="h-3.5 w-3.5 text-flow" aria-hidden />}
                  </div>
                  <div className="mt-1 truncate text-[12.5px] font-medium">{e.event_type}</div>
                  <div className="truncate text-[11px] text-muted">{e.service}</div>
                  <div className="mono mt-2 space-y-0.5 text-[10px] text-subtle">
                    <div>
                      hash <span className="text-fg">{pre(e.entry_hash)}</span>
                    </div>
                    <div>
                      prev <span className="text-muted">{pre(e.prev_hash)}</span>
                    </div>
                  </div>
                  {sealed && <div className="mt-1.5 text-[10px] font-medium text-muted">payload withheld (hash only)</div>}
                </button>
              </div>
            </motion.li>
          );
        })}
      </ol>
    </div>
  );
}
