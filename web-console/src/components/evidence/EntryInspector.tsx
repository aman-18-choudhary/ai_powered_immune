import { useEffect, useState, type ReactNode } from "react";
import { CheckCircle2, CircleHelp, EyeOff, XCircle } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Tooltip } from "@/components/ui/tooltip";
import { RefChip } from "@/components/common/RefChip";
import { checkPayloadHash, type HashCheck, type LedgerEntry } from "@/lib/evidence/ledger";
import { isRecord } from "@/types/guards";

export const FIELD_HELP: Record<string, string> = {
  call_ref: "Opaque reference to the call session (keyed hash, not a phone number).",
  chunk_count: "Transcript chunks scored before the alert fired.",
  crossed_ts: "When the session risk first crossed the alert threshold.",
  crossing: "Which threshold crossing this alert represents.",
  model_version: "Version of the scorer that produced the decision.",
  reason_codes: "Machine-readable reasons; see the Analyst page for meanings.",
  score: "Risk score from 0 to 1 at the time of the decision.",
  threshold: "Alert threshold the score was compared against.",
  amount_bucket: "Coarse amount band; the exact amount is not written to the ledger.",
  deadline_ts: "When the hold must be resolved by an analyst.",
  decision: "The decision taken: allow, step_up, hold_verify.",
  decision_seq: "Version counter of the decision; an upgrade increments it.",
  payee_ref: "Opaque reference to the receiving account (truncated keyed hash).",
  rail: "Payment rail: UPI, IMPS or NEFT.",
  txn_id: "Opaque transaction reference.",
  action: "What the analyst did: confirm_block or release.",
  antibody_id: "Identifier of the published threat-memory antibody.",
};

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[110px_1fr] gap-2 border-b border-line py-1.5 text-xs">
      <dt className="text-muted">{k}</dt>
      <dd className="mono min-w-0 break-all">{children}</dd>
    </div>
  );
}

function Json({ v, depth = 0, k }: { v: unknown; depth?: number; k?: string }): ReactNode {
  const pad = { paddingLeft: depth * 14 };
  const key = k !== undefined && (
    <Tooltip content={FIELD_HELP[k] ?? "Field in the signed payload."}>
      <span className="cursor-help text-intel underline decoration-dotted underline-offset-2">&quot;{k}&quot;</span>
    </Tooltip>
  );
  if (Array.isArray(v)) {
    return (
      <div style={pad}>
        {key}
        {key && ": "}[
        {v.map((x, i) => (
          <Json key={i} v={x} depth={depth + 1} />
        ))}
        ]
      </div>
    );
  }
  if (isRecord(v)) {
    return (
      <div style={pad}>
        {key}
        {key && ": "}
        {"{"}
        {Object.keys(v)
          .sort()
          .map((kk) => (
            <Json key={kk} k={kk} v={v[kk]} depth={depth + 1} />
          ))}
        {"}"}
      </div>
    );
  }
  return (
    <div style={pad}>
      {key}
      {key && ": "}
      <span className={typeof v === "string" ? "text-safe" : "text-flow"}>{JSON.stringify(v)}</span>
    </div>
  );
}

function HashNote({ c }: { c: HashCheck | null }) {
  if (!c) return <span className="text-muted">checking…</span>;
  if (c.state === "match")
    return (
      <span className="inline-flex items-center gap-1 text-safe">
        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> recomputed in this browser: matches payload_hash
      </span>
    );
  if (c.state === "mismatch")
    return (
      <span className="inline-flex items-center gap-1 text-danger-text">
        <XCircle className="h-3.5 w-3.5" aria-hidden /> recomputed {c.recomputed?.slice(0, 12)}…: does not match payload_hash
      </span>
    );
  if (c.state === "withheld")
    return (
      <span className="inline-flex items-center gap-1 text-muted">
        <EyeOff className="h-3.5 w-3.5" aria-hidden /> payload withheld: only its hash is held, nothing to recompute
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 text-muted">
      <CircleHelp className="h-3.5 w-3.5" aria-hidden /> not recomputable in this browser (needs a secure context); use the offline verifier
    </span>
  );
}

export function EntryInspector({ entry, sealedInPackage, onClose }: { entry: LedgerEntry | null; sealedInPackage: boolean; onClose: () => void }) {
  const [check, setCheck] = useState<HashCheck | null>(null);
  useEffect(() => {
    setCheck(null);
    if (!entry) return;
    let live = true;
    void checkPayloadHash(entry).then((c) => live && setCheck(c));
    return () => {
      live = false;
    };
  }, [entry]);
  return (
    <Dialog open={!!entry} onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        className="left-auto right-0 top-0 h-dvh max-h-dvh w-[min(96vw,540px)] translate-x-0 translate-y-0 rounded-none rounded-l-2xl"
        data-testid="inspector"
      >
        {entry && (
          <>
            <DialogTitle className="text-lg font-semibold">
              Entry #{entry.seq} · {entry.event_type}
            </DialogTitle>
            <DialogDescription className="text-sm text-muted">
              Envelope fields are chained; the payload is bound to the envelope only through payload_hash.
            </DialogDescription>
            {sealedInPackage && (
              <p className="mt-3 flex items-start gap-2 rounded-lg border border-line-strong bg-chip px-3 py-2 text-xs">
                <EyeOff className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                In the exported case package this payload is withheld (redacted context). The chain still binds its payload_hash.
              </p>
            )}
            <dl className="mt-4">
              <Row k="seq">{entry.seq}</Row>
              <Row k="ts">{entry.ts} <span className="text-subtle">(simulated clock)</span></Row>
              <Row k="service">{entry.service}</Row>
              <Row k="actor">{entry.actor}</Row>
              <Row k="model_version">{entry.model_version || "n/a"}</Row>
              <Row k="payload_hash">{entry.payload_hash}</Row>
              <Row k="prev_hash">{entry.prev_hash}</Row>
              <Row k="entry_hash">{entry.entry_hash}</Row>
              <Row k="case_refs">
                <span className="flex flex-wrap gap-1.5">
                  {entry.case_refs.length === 0 && <span className="text-subtle">none</span>}
                  {entry.case_refs.map((r) => (
                    <RefChip key={r} value={r} n={18} />
                  ))}
                </span>
              </Row>
            </dl>
            <h3 className="mt-5 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">Payload</h3>
            <p className="mt-1 text-xs" data-testid="hash-note">
              <HashNote c={check} />
            </p>
            {entry.payload_present ? (
              <pre tabIndex={0} aria-label="Payload JSON" className="mono mt-2 overflow-x-auto rounded-xl border border-line bg-sunken p-3 text-[12px] leading-relaxed" data-testid="payload-json">
                <Json v={entry.payload} />
              </pre>
            ) : (
              <p className="mt-2 rounded-xl border border-dashed border-line-strong p-3 text-sm text-muted">payload withheld (hash only)</p>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
