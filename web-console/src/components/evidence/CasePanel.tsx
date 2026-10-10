import { useEffect, useId, useState } from "react";
import { BadgeCheck, Download, FolderPlus, Loader2, PackageCheck, ShieldCheck, ShieldQuestion, ShieldX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/common/Panel";
import { RefChip } from "@/components/common/RefChip";
import { useEvidence, HERO_CASE } from "@/state/evidence";
import { CASE_ID_RE, validRef } from "@/lib/evidence/ledger";
import { verifyView } from "@/lib/evidence/verify";
import { formatBytes } from "@/lib/format";
import { canSeeOfficerViews } from "@/state/session";
import { useSession } from "@/state/session";
import { cn } from "@/lib/utils/cn";

interface Props {
  defaultRefs: string[];
  /** Entries the last verification reported as redacted. */
  onVerified?: () => void;
}

export function CasePanel({ defaultRefs, onVerified }: Props) {
  const role = useSession((s) => s.role);
  const ev = useEvidence();
  const id = useId();
  const [caseId, setCaseId] = useState(HERO_CASE);
  const [title, setTitle] = useState("");
  const [extra, setExtra] = useState("");
  const [extras, setExtras] = useState<string[]>([]);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const allowed = role === "officer" || role === "admin";
  const idOk = CASE_ID_RE.test(caseId);
  const refs = [...defaultRefs.filter((r) => !excluded.has(r)), ...extras];

  useEffect(() => {
    if (ev.verify) onVerified?.();
  }, [ev.verify, onVerified]);

  const addExtra = () => {
    const r = extra.trim();
    if (validRef(r) && !refs.includes(r) && extras.length < 5) {
      setExtras([...extras, r]);
      setExtra("");
    }
  };
  const view = ev.verify ? verifyView(ev.verify) : null;
  const built = ev.caseView;

  if (!canSeeOfficerViews(role) || !allowed) {
    return (
      <Panel title="Case and package" icon={<PackageCheck className="h-3.5 w-3.5" aria-hidden />}>
        <p className="p-4 text-sm text-muted" data-testid="case-readonly">
          Building a case, exporting the package and running the verifier are officer or admin actions. The {role ?? "current"} role can inspect the chain.
        </p>
      </Panel>
    );
  }

  return (
    <Panel title="Case, package and verification" icon={<PackageCheck className="h-3.5 w-3.5" aria-hidden />} bodyClassName="space-y-5 p-4">
      <section aria-label="Case builder" className="space-y-3">
        <h3 className="text-sm font-semibold">1. Case</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor={`${id}-id`} className="text-xs text-muted">
              Case id
            </label>
            <input
              id={`${id}-id`}
              value={caseId}
              onChange={(e) => setCaseId(e.target.value.toLowerCase())}
              aria-invalid={!idOk}
              className="mono mt-1 w-full rounded-lg border border-line-strong bg-sunken px-2.5 py-1.5 text-sm outline-none focus-visible:border-intel"
              data-testid="case-id"
            />
            {!idOk && <p className="mt-1 text-xs text-danger-text">Lowercase letters, digits, - or _, 2 to 48 characters.</p>}
          </div>
          <div>
            <label htmlFor={`${id}-t`} className="text-xs text-muted">
              Title (optional; the server default is used when empty)
            </label>
            <input
              id={`${id}-t`}
              value={title}
              maxLength={120}
              onChange={(e) => setTitle(e.target.value)}
              className="mt-1 w-full rounded-lg border border-line-strong bg-sunken px-2.5 py-1.5 text-sm outline-none focus-visible:border-intel"
            />
          </div>
        </div>
        <div>
          <p className="text-xs text-muted">
            References (defaults from this run: the first held transfer, its payee and the call that influenced it)
          </p>
          <ul className="mt-1.5 flex flex-wrap gap-2" data-testid="case-refs">
            {defaultRefs.length === 0 && <li className="text-xs text-subtle">No held transfer in the ledger yet: start a run first.</li>}
            {defaultRefs.map((r) => (
              <li key={r}>
                <label className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-chip px-2 py-1 text-xs">
                  <input
                    type="checkbox"
                    checked={!excluded.has(r)}
                    onChange={() => {
                      const n = new Set(excluded);
                      if (n.has(r)) n.delete(r);
                      else n.add(r);
                      setExcluded(n);
                    }}
                  />
                  <span className="mono">{r.length > 26 ? `${r.slice(0, 24)}…` : r}</span>
                </label>
              </li>
            ))}
            {extras.map((r) => (
              <li key={r}>
                <button type="button" onClick={() => setExtras(extras.filter((x) => x !== r))} className="mono rounded-lg border border-intel/40 bg-intel-soft px-2 py-1 text-xs" aria-label={`Remove reference ${r}`}>
                  {r} ×
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-2 flex gap-2">
            <input
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addExtra())}
              placeholder="add a reference (txn_…, payee_ref:…, call_ref:…)"
              aria-label="Add a case reference"
              className="mono min-w-0 flex-1 rounded-lg border border-line-strong bg-sunken px-2.5 py-1.5 text-xs outline-none focus-visible:border-intel"
            />
            <Button size="sm" variant="secondary" onClick={addExtra} disabled={!validRef(extra.trim())}>
              Add
            </Button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="intel"
            disabled={!idOk || ev.building}
            onClick={() =>
              void ev.buildCase({
                case_id: caseId,
                ...(title.trim() ? { title: title.trim() } : {}),
                ...(refs.length > 0 && (excluded.size > 0 || extras.length > 0) ? { case_refs: refs } : {}),
              })
            }
            data-testid="build-case"
          >
            {ev.building ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <FolderPlus className="h-4 w-4" aria-hidden />}
            Build case
          </Button>
          {built && (
            <span className="text-xs text-muted" data-testid="case-built">
              Case <span className="mono text-fg">{built.case_id}</span> ready with {built.case_refs.length} references
            </span>
          )}
        </div>
        {ev.caseError && (
          <p role="alert" className="text-xs text-danger-text">
            {ev.caseError}
          </p>
        )}
        {built && (
          <ul className="flex flex-wrap gap-1.5">
            {built.case_refs.map((r) => (
              <li key={r}>
                <RefChip value={r} n={16} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Package export" className="space-y-2 border-t border-line pt-4">
        <h3 className="text-sm font-semibold">2. Signed package</h3>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={() => void ev.exportPackage()} disabled={!built || ev.prep === "working"} data-testid="export-package">
            {ev.prep === "working" ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <PackageCheck className="h-4 w-4" aria-hidden />}
            Export package
          </Button>
          <Button variant="ghost" onClick={ev.download} disabled={!ev.blob} data-testid="download-package">
            <Download className="h-4 w-4" aria-hidden /> Download zip
          </Button>
        </div>
        {ev.prep === "error" && (
          <p role="alert" className="text-xs text-danger-text">
            {ev.prepError}
          </p>
        )}
        {ev.size !== null && (
          <dl className="grid grid-cols-[80px_1fr] gap-1 text-xs" data-testid="package-meta">
            <dt className="text-muted">Size</dt>
            <dd className="mono">{formatBytes(ev.size)}</dd>
            <dt className="text-muted">ETag</dt>
            <dd className="mono break-all">{ev.etag ?? "not provided"}</dd>
          </dl>
        )}
        <p className="text-[11px] text-subtle">Each export appends a package.exported entry to the ledger, so the chain grows by one.</p>
      </section>

      <section aria-label="Verification" className="space-y-3 border-t border-line pt-4">
        <h3 className="text-sm font-semibold">3. Verify offline</h3>
        <Button variant="primary" onClick={() => void ev.runVerify()} disabled={!built || ev.verifying} data-testid="verify-run">
          {ev.verifying ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <BadgeCheck className="h-4 w-4" aria-hidden />}
          Verify package
        </Button>
        <p className="text-xs text-muted">
          Runs the verifier shipped inside the package against the ledger&apos;s pinned public key, in a separate process.
        </p>
        {ev.verifyError && (
          <p role="alert" className="text-xs text-danger-text">
            {ev.verifyError}
          </p>
        )}
        {view && ev.verify && (
          <div
            className={cn(
              "rounded-xl border p-3",
              view.tone === "safe" && "border-safe/40 bg-safe-soft",
              view.tone === "warn" && "border-warn/40 bg-warn-soft",
              view.tone === "danger" && "border-danger/40 bg-danger-soft",
            )}
            data-testid="verify-result"
            role="status"
          >
            <div className="flex items-center gap-2 font-semibold">
              {view.tone === "safe" ? <ShieldCheck className="h-5 w-5 text-safe" aria-hidden /> : view.tone === "warn" ? <ShieldQuestion className="h-5 w-5 text-warn" aria-hidden /> : <ShieldX className="h-5 w-5 text-danger-text" aria-hidden />}
              <span data-testid="verify-status">{ev.verify.status}</span>
              <span className="text-sm font-normal text-muted">{view.title}</span>
            </div>
            <p className="mt-1 text-sm">{view.summary}</p>
            <p className="mt-1 text-xs text-muted" data-testid="exit-meaning">
              {view.exit.meaning}
            </p>
            <ul className="mono mt-2 space-y-0.5 rounded-lg border border-line bg-sunken p-2 text-[11.5px]" aria-label="Verifier output">
              {view.lines.map((l, i) => (
                <li key={i} className={cn(l.kind === "pass" && "text-safe", l.kind === "fail" && "text-danger-text")}>
                  {l.text}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[11px] text-subtle">
              {ev.verify.entries} entries · {ev.verify.checkpoints ?? 0} signed checkpoint(s) · {ev.verify.redacted_seqs.length} redacted · pinned key{" "}
              <span className="mono">{ev.verify.pinned_key_id}</span>
            </p>
          </div>
        )}
      </section>
    </Panel>
  );
}
