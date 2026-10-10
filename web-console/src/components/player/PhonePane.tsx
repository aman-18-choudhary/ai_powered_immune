import { useEffect, useMemo, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { PhoneCall, PhoneIncoming, ShieldAlert, ShieldCheck, ShieldQuestion, Signal } from "lucide-react";
import { Panel } from "@/components/common/Panel";
import { useMotionPrefs } from "@/lib/motion/prefs";
import { useTypedStream } from "@/lib/motion/useTypedStream";
import { formatBucket } from "@/lib/format";
import { victimCalls, type RunState } from "@/state/reducer";
import { cn } from "@/lib/utils/cn";

interface Props {
  run: RunState;
}

const THRESHOLD_FALLBACK = 0.7;

function ScoreMeter({ score, threshold, flagged }: { score: number; threshold: number; flagged: boolean }) {
  const pct = Math.round(Math.min(1, Math.max(0, score)) * 100);
  return (
    <div className="space-y-1" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="Scam score">
      <div className="flex items-baseline justify-between text-[11px]">
        <span className="font-medium text-muted">Scam score</span>
        <span className={cn("mono tnum font-semibold", flagged ? "text-danger-text" : "text-fg")}>{score.toFixed(2)}</span>
      </div>
      <div className="relative h-2 overflow-hidden rounded-full bg-chip">
        <motion.div
          className={cn("h-full rounded-full", flagged ? "bg-danger" : score > 0.35 ? "bg-warn" : "bg-safe")}
          initial={false}
          animate={{ width: `${pct}%` }}
          transition={{ type: "spring", stiffness: 120, damping: 20 }}
        />
        <span
          className="absolute top-0 h-full w-px bg-fg/70"
          style={{ left: `${threshold * 100}%` }}
          title={`Alert threshold ${threshold}`}
          aria-hidden
        />
      </div>
    </div>
  );
}

export function PhonePane({ run }: Props) {
  const { reduced } = useMotionPrefs();
  const calls = useMemo(() => victimCalls(run, "A"), [run]);
  const items = useMemo(() => calls.flatMap((c) => c.chunks.map((ch) => ({ id: ch.id, text: ch.text }))), [calls]);
  const revealed = useTypedStream(items, reduced);
  const scroller = useRef<HTMLDivElement>(null);
  const revealedTotal = Object.values(revealed).reduce((a, b) => a + b, 0);
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [revealedTotal, items.length]);
  const latest = calls[calls.length - 1];
  const alert = calls.find((c) => c.alert)?.alert ?? null;
  const threshold = alert?.threshold ?? THRESHOLD_FALLBACK;
  const score = latest?.score ?? 0;
  const flagged = !!alert;

  // The victim's own transfer attempt(s), joined with their hold state.
  const attempts = useMemo(
    () =>
      run.txns
        .filter((t) => t.victim === "A")
        .map((t) => ({ txn: t, hold: run.holds[t.txn_ref] })),
    [run.txns, run.holds],
  );

  return (
    <Panel
      title="Victim A · phone"
      icon={<PhoneIncoming className="h-3.5 w-3.5" aria-hidden />}
      className="h-full"
      bodyClassName="flex items-start justify-center overflow-hidden p-3"
      aria-label="Victim A phone"
    >
      <div
        className="relative flex h-full max-h-[560px] w-full max-w-[290px] flex-col rounded-[34px] border-[6px] p-0 shadow-[0_30px_60px_-30px_rgb(0_0_0/0.8)]"
        style={{ background: "var(--phone-screen)", borderColor: "var(--phone-bezel)" }}
      >
        <div className="absolute left-1/2 top-0 z-10 h-4 w-20 -translate-x-1/2 rounded-b-2xl" style={{ background: "var(--phone-bezel)" }} aria-hidden />
        <div className="flex items-center justify-between px-5 pt-2 text-[10px] text-muted">
          <span className="mono">{run.hasData ? "now" : "--:--"}</span>
          <Signal className="h-3 w-3" aria-hidden />
        </div>
        <div className={cn("mx-3 mt-3 rounded-2xl border p-3", flagged ? "border-danger/50 bg-danger-soft" : "border-line bg-chip")}>
          <div className="flex items-center gap-2">
            <span className={cn("grid h-8 w-8 place-items-center rounded-full", flagged ? "bg-danger text-white" : "bg-intel-soft text-intel")}>
              <PhoneCall className="h-4 w-4" aria-hidden />
            </span>
            <div className="min-w-0 leading-tight">
              <div className="truncate text-xs font-semibold">Unknown caller</div>
              <div className="text-[10px] text-muted">{latest ? `${latest.lang ?? "call"} · simulated` : "Waiting for a call…"}</div>
            </div>
          </div>
          <div className="mt-3">
            <ScoreMeter score={score} threshold={threshold} flagged={flagged} />
          </div>
        </div>

        <AnimatePresence>
          {flagged && (
            <motion.div
              key="alert"
              initial={{ opacity: 0, y: -8, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              className="mx-3 mt-2 flex items-start gap-2 rounded-xl border border-danger bg-danger px-3 py-2 text-white"
              role="alert"
            >
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <div className="text-[11.5px] leading-snug">
                <div className="font-semibold">Likely scam call</div>
                <div className="opacity-90">Call-guard flagged this call. Do not transfer money. Hang up.</div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <div ref={scroller} className="mt-2 min-h-0 flex-1 space-y-1.5 overflow-y-auto px-3 pb-2" aria-live="off" aria-label="Call transcript">
          {items.length === 0 && (
            <p className="px-1 pt-4 text-center text-[11px] text-subtle">Call transcript appears here as the call unfolds.</p>
          )}
          {calls.map((c, ci) => (
            <div key={c.ref} className="space-y-1.5">
              {ci > 0 && <div className="py-1 text-center text-[10px] uppercase tracking-wider text-subtle">second call</div>}
              {c.chunks.map((ch) => {
                const n = revealed[ch.id] ?? 0;
                const text = ch.text.slice(0, n);
                const typing = n < ch.text.length;
                if (n === 0) return null;
                return (
                  <motion.div
                    key={ch.id}
                    initial={reduced ? false : { opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="max-w-[92%] rounded-2xl rounded-bl-md border border-line bg-chip px-2.5 py-1.5 text-[11.5px] leading-snug"
                  >
                    <span className="mr-1 text-[9px] font-semibold uppercase tracking-wide text-subtle">{ch.speaker}</span>
                    {text}
                    {typing && <span className="caret ml-0.5" aria-hidden />}
                  </motion.div>
                );
              })}
            </div>
          ))}
        </div>

        <div className="space-y-1.5 border-t border-line px-3 py-2">
          {attempts.length === 0 ? (
            <div className="flex items-center gap-1.5 text-[11px] text-subtle">
              <ShieldQuestion className="h-3.5 w-3.5" aria-hidden /> No transfer attempted yet
            </div>
          ) : (
            attempts.slice(-2).map(({ txn, hold }) => {
              const blocked = hold?.state === "blocked";
              const held = hold && hold.state === "open";
              return (
                <motion.div
                  key={txn.txn_ref}
                  initial={reduced ? false : { opacity: 0, x: 12 }}
                  animate={{ opacity: 1, x: 0 }}
                  className={cn(
                    "flex items-center gap-2 rounded-xl border px-2.5 py-1.5 text-[11px]",
                    blocked ? "border-safe/40 bg-safe-soft" : held ? "border-warn/50 bg-warn-soft" : "border-line bg-chip",
                  )}
                >
                  {blocked ? (
                    <ShieldCheck className="h-4 w-4 shrink-0 text-safe" aria-hidden />
                  ) : (
                    <ShieldAlert className="h-4 w-4 shrink-0 text-warn" aria-hidden />
                  )}
                  <div className="min-w-0 leading-tight">
                    <div className="font-semibold">
                      {blocked ? "Transfer blocked" : held ? "Held for verification" : "Transfer attempt"}
                    </div>
                    <div className="text-muted">
                      {txn.rail} · {formatBucket(txn.bucket)}
                    </div>
                  </div>
                </motion.div>
              );
            })
          )}
        </div>
      </div>
    </Panel>
  );
}
