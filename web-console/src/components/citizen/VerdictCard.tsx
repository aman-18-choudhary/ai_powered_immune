import { useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { AlertOctagon, AlertTriangle, CheckCircle2, Flag, HelpCircle, ListChecks, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { t, type Lang } from "@/lib/i18n/citizen";
import type { Label, Verdict } from "@/lib/citizen/verdict";
import { cn } from "@/lib/utils/cn";

const LOOK: Record<Label, { Icon: LucideIcon; tone: string; ring: string; head: "verdictSafe" | "verdictSuspicious" | "verdictScam"; sub: "safeSub" | "suspiciousSub" | "scamSub" }> = {
  safe: { Icon: CheckCircle2, tone: "text-safe border-safe/40 bg-safe-soft", ring: "var(--safe)", head: "verdictSafe", sub: "safeSub" },
  suspicious: { Icon: AlertTriangle, tone: "text-warn border-warn/40 bg-warn-soft", ring: "var(--warn)", head: "verdictSuspicious", sub: "suspiciousSub" },
  likely_scam: { Icon: AlertOctagon, tone: "text-danger-text border-danger/40 bg-danger-soft", ring: "var(--danger)", head: "verdictScam", sub: "scamSub" },
};

export function ScoreRing({ score, color, label }: { score: number; color: string; label: string }) {
  const size = 84;
  const r = 34;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative grid shrink-0 place-items-center" style={{ width: size, height: size }} role="img" aria-label={label}>
      <svg width={size} height={size} viewBox="0 0 84 84" className="-rotate-90" aria-hidden>
        <circle cx="42" cy="42" r={r} fill="none" stroke="var(--border-strong)" strokeWidth="7" />
        <motion.circle
          cx="42" cy="42" r={r} fill="none" stroke={color} strokeWidth="7" strokeLinecap="round" strokeDasharray={c}
          initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c * (1 - score) }} transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
        />
      </svg>
      <span className="mono tnum absolute text-lg font-semibold">{Math.round(score * 100)}</span>
    </div>
  );
}

interface Props {
  lang: Lang;
  verdict: Verdict;
  truncated: boolean;
  reported: boolean;
  onReport: () => void;
  onAgain: () => void;
}

export function VerdictCard({ lang, verdict, truncated, reported, onReport, onAgain }: Props) {
  const look = LOOK[verdict.label];
  const head = useRef<HTMLHeadingElement>(null);
  useEffect(() => head.current?.focus(), []);
  return (
    <motion.section
      initial={{ opacity: 0, y: 14, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 240, damping: 24 }}
      aria-labelledby="verdict-h"
      className="space-y-4"
      data-testid="verdict"
      data-label={verdict.label}
    >
      <div className={cn("rounded-3xl border p-5", look.tone)}>
        <div className="flex items-center gap-4">
          <ScoreRing score={verdict.score} color={look.ring} label={`${t(lang, "scoreLabel")}: ${t(lang, "scoreOf", { n: Math.round(verdict.score * 100) })}`} />
          <div className="min-w-0">
            <look.Icon className="mb-1 h-7 w-7" aria-hidden />
            <h2 id="verdict-h" ref={head} tabIndex={-1} className="text-2xl font-bold leading-tight outline-none" data-testid="verdict-label">
              {t(lang, look.head)}
            </h2>
          </div>
        </div>
        <p className="mt-3 text-[15px] leading-snug text-fg">{t(lang, look.sub)}</p>
      </div>

      {(verdict.uncertain || truncated) && (
        <div className="rounded-2xl border border-line-strong bg-chip p-4 text-sm" role="note" data-testid="uncertain">
          <p className="flex items-center gap-2 font-semibold">
            <HelpCircle className="h-4 w-4" aria-hidden /> {t(lang, "uncertainTitle")}
          </p>
          {verdict.uncertain && <p className="mt-1 text-muted">{t(lang, "uncertainBody")}</p>}
          {truncated && <p className="mt-1 text-muted">{t(lang, "truncatedBody", { max: 4000 })}</p>}
        </div>
      )}

      <div className="rounded-2xl border border-line bg-chip p-4">
        <h3 className="text-sm font-semibold">{t(lang, "whyTitle")}</h3>
        {verdict.reasons.length === 0 ? (
          <p className="mt-2 text-sm text-muted">{t(lang, "noReasons")}</p>
        ) : (
          <ul className="mt-2 space-y-2.5">
            {verdict.reasons.map((r) => (
              <li key={r.code} className="flex gap-2 text-sm leading-snug">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-intel" aria-hidden />
                <span>{r.text}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {verdict.steps.length > 0 && (
        <div className="rounded-2xl border border-line bg-chip p-4">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <ListChecks className="h-4 w-4 text-flow" aria-hidden /> {t(lang, "stepsTitle")}
          </h3>
          <ol className="mt-2 space-y-2.5">
            {verdict.steps.map((s, i) => (
              <li key={i} className="flex gap-3 text-sm leading-snug">
                <span className="mono grid h-6 w-6 shrink-0 place-items-center rounded-full bg-raised text-xs font-semibold">{i + 1}</span>
                <span className="pt-0.5">{s}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="space-y-2">
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={onReport} disabled={reported} data-testid="report">
            <Flag className="h-4 w-4" aria-hidden /> {t(lang, "report")}
          </Button>
          <Button variant="ghost" onClick={onAgain} data-testid="again">
            {t(lang, "another")}
          </Button>
        </div>
        <p className="text-xs text-subtle">{t(lang, "reportNote")}</p>
        {reported && (
          <p role="status" className="rounded-xl border border-line bg-chip p-3 text-sm" data-testid="report-ack">
            {t(lang, "reportDone")} {t(lang, "reportHelp")}
          </p>
        )}
      </div>
    </motion.section>
  );
}
