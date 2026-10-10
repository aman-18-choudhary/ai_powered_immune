import { useMemo } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, CircleSlash, Landmark, Lock, ShieldAlert, ShieldCheck, Sparkles, UserCheck } from "lucide-react";
import { Panel } from "@/components/common/Panel";
import { CountdownRing } from "@/components/common/CountdownRing";
import { ReasonChips } from "@/components/common/ReasonChips";
import { RefChip } from "@/components/common/RefChip";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { bankLabel, formatBucket, formatLatencyMs } from "@/lib/format";
import { remainingSeconds } from "@/lib/format/countdown";
import { useMotionPrefs } from "@/lib/motion/prefs";
import { holdsList, victimCalls, type HoldRecord, type RunState, type TxnView } from "@/state/reducer";
import { cn } from "@/lib/utils/cn";

interface Props {
  run: RunState;
  bank: "bank_a" | "bank_b";
  simT: number;
  canResolve: boolean;
  onConfirm?: (h: HoldRecord) => void;
  confirming?: boolean;
}

function DecisionBadge({ decision, blocked }: { decision: string; blocked?: boolean }) {
  if (blocked) {
    return (
      <span className="inline-flex items-center gap-1 rounded-md border border-safe/40 bg-safe-soft px-1.5 py-0.5 text-[10.5px] font-medium text-safe">
        <ShieldCheck className="h-3 w-3" aria-hidden /> Blocked
      </span>
    );
  }
  if (decision === "allow") {
    return (
      <span className="inline-flex items-center gap-1 rounded-md border border-line bg-chip px-1.5 py-0.5 text-[10.5px] font-medium text-muted">
        <CheckCircle2 className="h-3 w-3" aria-hidden /> Allowed
      </span>
    );
  }
  const label = decision === "step_up" ? "Step-up" : decision === "block" ? "Blocked" : "Held";
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-warn/40 bg-warn-soft px-1.5 py-0.5 text-[10.5px] font-medium text-warn">
      <ShieldAlert className="h-3 w-3" aria-hidden /> {label}
    </span>
  );
}

function TxnRow({ t, hold }: { t: TxnView; hold?: HoldRecord }) {
  const { reduced } = useMotionPrefs();
  const blocked = hold?.state === "blocked";
  return (
    <motion.li
      layout={!reduced}
      initial={reduced ? false : { opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex items-center gap-2 rounded-lg border border-line bg-chip px-2 py-1.5 text-[11.5px]"
    >
      <DecisionBadge decision={t.decision} blocked={blocked} />
      <span className="text-muted">{t.rail}</span>
      <span className="font-medium">{formatBucket(t.bucket)}</span>
      <span className="mono tnum ml-auto text-muted">{t.score.toFixed(2)}</span>
      {t.victim && <span className="rounded bg-intel-soft px-1 text-[10px] font-semibold text-intel">V{t.victim}</span>}
    </motion.li>
  );
}

function FeaturedHold({ h, simT }: { h: HoldRecord; simT: number }) {
  const total = Math.max(1, h.deadlineT - h.createdT);
  const resolved = h.state !== "open";
  const remaining = remainingSeconds(h.deadlineT, resolved && h.resolvedT !== undefined ? h.resolvedT : simT);
  const blocked = h.state === "blocked";
  return (
    <div className={cn("rounded-xl border p-3", blocked ? "border-safe/40 bg-safe-soft" : "border-warn/40 bg-warn-soft")}>
      <div className="flex items-center gap-3">
        <CountdownRing remaining={remaining} total={total} state={resolved ? "resolved" : "open"} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-[13px] font-semibold">
            {blocked ? <ShieldCheck className="h-4 w-4 text-safe" aria-hidden /> : <Lock className="h-4 w-4 text-warn" aria-hidden />}
            {blocked ? "Transfer blocked" : resolved ? "Hold released" : "Held for verification"}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
            <span>
              {h.rail} · {formatBucket(h.bucket)} · score <span className="mono tnum">{h.score.toFixed(2)}</span>
            </span>
            <RefChip value={h.txn_ref} />
          </div>
        </div>
      </div>
      <div className="mt-2">
        <ReasonChips reasons={h.reasons} max={4} />
      </div>
    </div>
  );
}

export function BankPane({ run, bank, simT, canResolve, onConfirm, confirming }: Props) {
  const { reduced } = useMotionPrefs();
  const name = bankLabel(bank, run.banks);
  const isA = bank === "bank_a";
  const txns = useMemo(() => run.txns.filter((t) => t.bank === bank).slice(-6).reverse(), [run.txns, bank]);
  const holds = useMemo(() => holdsList(run).filter((h) => h.bank === bank), [run, bank]);
  const featured = holds.find((h) => h.hero) ?? holds[0];
  const antibody = run.antibodies[0];
  const applied = antibody?.applied.find((a) => a.bank === bank);
  const callsB = useMemo(() => victimCalls(run, "B"), [run]);
  const maxB = callsB.reduce((m, c) => Math.max(m, ...c.chunks.map((x) => x.score), 0), 0);
  const antibodyHit = featured?.reasons.find((r) => r.code === "ANTIBODY_MATCH");

  return (
    <Panel
      title={`${isA ? "Bank A" : "Bank B"} · ${name} console`}
      icon={<Landmark className="h-3.5 w-3.5" aria-hidden />}
      className="h-full"
      bodyClassName="flex flex-col gap-3 overflow-y-auto p-3 [&>*]:shrink-0"
      aria-label={`${isA ? "Bank A" : "Bank B"} console`}
    >
      {!isA && (
        <div className="rounded-xl border border-line bg-chip p-2.5 text-[11.5px]">
          <div className="flex items-center gap-1.5 font-medium">
            <CircleSlash className="h-3.5 w-3.5 text-subtle" aria-hidden />
            Victim B&apos;s call
          </div>
          <p className="mt-0.5 text-muted">
            {callsB.length === 0
              ? "Waiting for a second call…"
              : maxB < 0.7
                ? `Call-guard did not alert (highest score ${maxB.toFixed(2)}). A constructed miss: this bank has no call signal.`
                : `Call-guard alerted (score ${maxB.toFixed(2)}).`}
          </p>
        </div>
      )}

      <AnimatePresence initial={false}>
        {!isA && applied && antibody && (
          <motion.div
            key="applied"
            initial={reduced ? false : { opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            className="relative overflow-hidden rounded-xl border border-intel/50 bg-intel-soft p-3"
          >
            {!reduced && (
              <span className="pointer-events-none absolute -left-4 top-1/2 h-10 w-10 -translate-y-1/2" aria-hidden>
                <span className="pulse-ring absolute inset-0 rounded-full border border-intel" />
                <span className="pulse-ring absolute inset-0 rounded-full border border-intel [animation-delay:0.7s]" />
              </span>
            )}
            <div className="relative flex items-center gap-2.5 pl-6">
              <Sparkles className="h-4 w-4 shrink-0 text-intel" aria-hidden />
              <div className="min-w-0 leading-tight">
                <div className="text-[13px] font-semibold text-intel">Antibody applied</div>
                <div className="text-[11px] text-muted">
                  from {bankLabel(antibody.bank, run.banks)} · measured latency{" "}
                  <span className="mono tnum font-semibold text-fg">{formatLatencyMs(applied.latency_ms)}</span>
                </div>
              </div>
              <RefChip value={antibody.ref} className="ml-auto" />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {featured ? (
        <FeaturedHold h={featured} simT={simT} />
      ) : (
        <div className="rounded-xl border border-dashed border-line px-3 py-4 text-center text-[12px] text-subtle">
          {isA ? "Waiting for a suspicious transfer…" : "Waiting for victim B's transfer…"}
        </div>
      )}

      {!isA && featured && antibodyHit && (
        <div className="flex items-start gap-2 rounded-xl border border-intel/40 bg-intel-soft px-3 py-2 text-[12px]">
          <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-intel" aria-hidden />
          <div>
            <div className="font-semibold">Blocked by shared antibody</div>
            <div className="text-muted">
              Reason <span className="mono rounded border border-intel/50 bg-intel-soft px-1 text-intel">ANTIBODY_MATCH</span>: decision{" "}
              <span className="mono">{featured.decision}</span>, funds do not move until verified.
            </div>
          </div>
        </div>
      )}

      {isA && featured && (
        <div className="rounded-xl border border-line bg-chip p-3">
          <div className="flex items-center gap-2 text-[12px] font-semibold">
            <UserCheck className="h-4 w-4 text-intel" aria-hidden /> Analyst confirmation
          </div>
          {featured.state === "open" ? (
            <>
              <p className="mt-1 text-[11.5px] text-muted">
                Auto-confirms when the review window closes (
                <span className="mono tnum">{Math.max(0, Math.ceil(featured.deadlineT - simT))}s</span> simulated left), or confirm now.
              </p>
              <div className="mt-2">
                <Tooltip content={canResolve ? "Confirm this payee as a mule and share an antibody" : "Requires the analyst or admin role"}>
                  <span className="inline-block">
                    <Button
                      variant="intel"
                      size="sm"
                      disabled={!canResolve || confirming}
                      onClick={() => onConfirm?.(featured)}
                      data-testid="confirm-mule"
                    >
                      <UserCheck className="h-3.5 w-3.5" aria-hidden /> Confirm mule
                    </Button>
                  </span>
                </Tooltip>
              </div>
            </>
          ) : (
            <p className="mt-1 text-[11.5px] text-muted">
              {featured.state === "blocked" ? "Mule confirmed" : "Released"} by{" "}
              <span className="mono">{featured.resolvedBy ?? "analyst"}</span> ({featured.source === "manual" ? "manual" : "automatic"}).
            </p>
          )}
        </div>
      )}

      {isA && antibody && (
        <motion.div
          initial={reduced ? false : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-2 rounded-xl border border-intel/50 bg-intel-soft px-3 py-2 text-[12px]"
        >
          <Sparkles className="h-4 w-4 text-intel" aria-hidden />
          <span className="font-semibold text-intel">Antibody published</span>
          <span className="text-muted">{antibody.kind.replace(/_/g, " ")}</span>
          <RefChip value={antibody.ref} className="ml-auto" />
        </motion.div>
      )}

      <div>
        <h3 className="mb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-subtle">Scored transactions</h3>
        {txns.length === 0 ? (
          <p className="text-[11.5px] text-subtle">Waiting for events…</p>
        ) : (
          <ul className="space-y-1.5">
            {txns.map((t) => (
              <TxnRow key={t.txn_ref} t={t} hold={run.holds[t.txn_ref]} />
            ))}
          </ul>
        )}
      </div>
    </Panel>
  );
}
