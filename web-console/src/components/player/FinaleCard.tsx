import { useEffect } from "react";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { BadgeCheck, Download, FileSearch, Loader2, ShieldAlert, ShieldCheck, ShieldX, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatBytes, formatLatencyMs } from "@/lib/format";
import { useEvidence } from "@/state/evidence";
import type { FinishedRecord } from "@/state/reducer";
import type { VerifyStatus } from "@/types/api";
import { cn } from "@/lib/utils/cn";
import { popIn } from "@/lib/motion/variants";

export const VERIFY_LABEL: Record<VerifyStatus, { label: string; hint: string; tone: "safe" | "warn" | "danger" }> = {
  PASS_PINNED: { label: "PASS_PINNED", hint: "Integrity verified and signed by the pinned ledger key", tone: "safe" },
  INTEGRITY_OK_UNPINNED: {
    label: "UNPINNED",
    hint: "Integrity is consistent, but the signing key was not pinned to the ledger",
    tone: "warn",
  },
  FAIL: { label: "FAIL", hint: "The package did not verify", tone: "danger" },
};

interface Props {
  runId: string | null;
  finished: FinishedRecord;
  canExport: boolean;
  onClose: () => void;
}

export function FinaleCard({ runId, finished, canExport, onClose }: Props) {
  const ev = useEvidence();
  const { prepare } = ev;
  const protectedB = finished.victimBProtected === true;

  useEffect(() => {
    if (canExport && runId && protectedB) void prepare(runId);
  }, [canExport, runId, protectedB, prepare]);

  const v = ev.verify ? VERIFY_LABEL[ev.verify.status] : null;
  const VIcon = v?.tone === "safe" ? ShieldCheck : v?.tone === "warn" ? ShieldAlert : ShieldX;

  return (
    <motion.section
      variants={popIn}
      initial="hidden"
      animate="show"
      role="dialog"
      aria-labelledby="finale-title"
      aria-modal="false"
      className="panel panel-blur relative max-h-full w-[min(94vw,640px)] overflow-auto p-6 shadow-2xl"
      style={{ boxShadow: "0 0 0 1px color-mix(in srgb, var(--safe) 35%, transparent), 0 30px 80px -30px color-mix(in srgb, var(--safe) 40%, transparent)" }}
    >
      <button
        type="button"
        onClick={onClose}
        aria-label="Close result"
        className="absolute right-3 top-3 rounded-lg p-1.5 text-muted hover:bg-hover hover:text-fg"
      >
        <X className="h-4 w-4" aria-hidden />
      </button>
      <div className="flex items-start gap-4">
        <span
          className={cn(
            "grid h-14 w-14 shrink-0 place-items-center rounded-2xl border",
            protectedB ? "border-safe/50 bg-safe-soft text-safe" : "border-warn/50 bg-warn-soft text-warn",
          )}
        >
          {protectedB ? <ShieldCheck className="h-7 w-7" aria-hidden /> : <ShieldAlert className="h-7 w-7" aria-hidden />}
        </span>
        <div>
          <h2 id="finale-title" className="text-2xl font-semibold tracking-tight">
            {protectedB ? "Victim B protected" : "Run complete: no antibody was shared"}
          </h2>
          <p className="mt-1 text-sm text-muted">
            {protectedB ? (
              <>
                Bank B held the transfer because Bank A&apos;s confirmed mule reached it as an antibody
                {finished.latencyMs != null && (
                  <>
                    , applied in <span className="mono font-semibold text-fg">{formatLatencyMs(finished.latencyMs)}</span> (measured)
                  </>
                )}
                .
              </>
            ) : (
              "Without a shared antibody, bank B had no memory of this mule."
            )}
          </p>
        </div>
      </div>

      <dl className="mt-5 grid grid-cols-3 gap-1.5 sm:gap-2">
        {[
          ["Antibody latency", formatLatencyMs(finished.latencyMs)],
          ["Ledger entries", finished.ledgerEntries !== undefined ? String(finished.ledgerEntries) : "—"],
          ["Package size", ev.size !== null ? formatBytes(ev.size) : ev.prep === "working" ? "…" : "—"],
        ].map(([k, val]) => (
          <div key={k} className="rounded-xl border border-line bg-chip px-3 py-2">
            <dt className="text-[10.5px] uppercase tracking-wider text-subtle">{k}</dt>
            <dd className="mono tnum mt-0.5 text-base font-semibold sm:text-lg">{val}</dd>
          </div>
        ))}
      </dl>

      {canExport ? (
        <div className="mt-5 flex flex-wrap gap-2">
          <Button variant="secondary" disabled={ev.prep !== "ready"} onClick={() => ev.download()} data-testid="download-package">
            {ev.prep === "working" ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Download className="h-4 w-4" aria-hidden />}
            Download evidence package
          </Button>
          <Button variant="primary" disabled={ev.verifying} onClick={() => void ev.runVerify()} data-testid="verify-offline">
            {ev.verifying ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <BadgeCheck className="h-4 w-4" aria-hidden />}
            Verify offline
          </Button>
          <Link to="/evidence">
            <Button variant="ghost">
              <FileSearch className="h-4 w-4" aria-hidden /> Open in Evidence
            </Button>
          </Link>
        </div>
      ) : (
        <p className="mt-5 rounded-lg border border-line bg-chip px-3 py-2 text-xs text-muted">
          Evidence export and verification need the officer or admin role.
        </p>
      )}

      {ev.prep === "error" && (
        <p role="alert" className="mt-3 text-xs text-danger-text">
          Could not prepare the package: {ev.prepError}
        </p>
      )}
      {ev.verifyError && (
        <p role="alert" className="mt-3 text-xs text-danger-text">
          Verification request failed: {ev.verifyError}
        </p>
      )}

      {ev.verify && v && (
        <div className="mt-4" data-testid="verify-result">
          <div
            className={cn(
              "inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm font-semibold",
              v.tone === "safe" && "border-safe/50 bg-safe-soft text-safe",
              v.tone === "warn" && "border-warn/50 bg-warn-soft text-warn",
              v.tone === "danger" && "border-danger/50 bg-danger-soft text-danger-text",
            )}
          >
            <VIcon className="h-4 w-4" aria-hidden />
            <span className="mono" data-testid="verify-status">
              {v.label}
            </span>
            <span className="text-xs font-normal text-muted">exit {ev.verify.exit_code}</span>
          </div>
          <p className="mt-1 text-xs text-muted">{v.hint}</p>
          <pre
            aria-label="Verifier output"
            className="mono mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-[var(--bg-sunken)] p-3 text-[11px] leading-relaxed text-muted"
          >
            {ev.verify.lines.join("\n")}
          </pre>
        </div>
      )}
    </motion.section>
  );
}
