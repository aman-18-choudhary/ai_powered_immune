import { useState, type FormEvent } from "react";
import { CheckCircle2, ClipboardList, History, ListChecks } from "lucide-react";
import { Panel } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { AnalysisPanel } from "./AnalysisPanel";
import { RelatedPanel } from "./RelatedPanel";
import { SimilarPanel } from "./SimilarPanel";
import { CaseTimeline, InvestigationNotes, ReportDownload } from "./CaseTools";
import { EditDetails } from "./EditDetails";
import { EvidenceManager } from "./EvidenceManager";
import { Field, RiskBadge, Spinner, StatusChip, inputCls } from "./ui";
import { complaintsApi, messageFor } from "@/lib/complaints/api";
import { CATEGORY_LABEL, NOT_PROVIDED, PAYMENT_LABEL, STATUS_LABEL, TRANSITIONS, formatAmount, formatDateTime, shortId } from "@/lib/complaints/format";
import type { AccountRole, ComplaintDetail, ComplaintStatus } from "@/lib/complaints/types";
import { cn } from "@/lib/utils/cn";

function Value({ v, mono }: { v: string | number | null | undefined; mono?: boolean }) {
  if (v === null || v === undefined || v === "") return <span className="italic text-subtle">{NOT_PROVIDED}</span>;
  return <span className={cn("break-words", mono && "mono")}>{v}</span>;
}

function Group({ title, rows }: { title: string; rows: [string, React.ReactNode][] }) {
  return (
    <section aria-label={title} className="space-y-1.5">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-subtle">{title}</h3>
      <dl className="grid gap-x-4 gap-y-1.5 sm:grid-cols-[minmax(0,180px)_minmax(0,1fr)]">
        {rows.map(([k, val]) => (
          <div key={k} className="contents">
            <dt className="text-sm text-muted">{k}</dt>
            <dd className="text-sm" data-field={k}>
              {val}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function StatusUpdate({ c, onUpdated }: { c: ComplaintDetail; onUpdated: (c: ComplaintDetail) => void }) {
  const options = TRANSITIONS[c.status] ?? [];
  const [to, setTo] = useState<ComplaintStatus | "">("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!to) {
      setError("Choose the new status.");
      return;
    }
    setBusy(true);
    setError(null);
    setDone("");
    try {
      const updated = await complaintsApi.setStatus(c.id, to, note.trim() || null);
      onUpdated(updated);
      setDone(`Status changed to ${STATUS_LABEL[updated.status]}. The complainant has been notified.`);
      setTo("");
      setNote("");
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Update status" icon={<ListChecks className="h-3.5 w-3.5" aria-hidden />} data-testid="status-update">
      <form onSubmit={submit} className="space-y-3 p-4" noValidate>
        <Field label="New status" error={error && !to ? error : undefined}>
          {(p) => (
            <select {...p} value={to} onChange={(e) => setTo(e.target.value as ComplaintStatus)} className={inputCls} disabled={busy} data-testid="status-select">
              <option value="">Choose…</option>
              {options.map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Note" optional hint={`Visible to the complainant. ${note.length}/500`}>
          {(p) => <textarea {...p} rows={3} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} className={cn(inputCls, "resize-y")} disabled={busy} data-testid="status-note" />}
        </Field>
        <Button type="submit" variant="primary" disabled={busy || options.length === 0} data-testid="status-submit">
          {busy && <Spinner />} Update status
        </Button>
        {error && to !== "" && (
          <p role="alert" className="rounded-lg border border-danger/40 bg-danger-soft p-2 text-sm" data-testid="status-error">
            {error}
          </p>
        )}
        <p role="status" aria-live="polite" className="text-sm text-safe" data-testid="status-done">
          {done}
        </p>
      </form>
    </Panel>
  );
}

/** The full complaint (owner or staff view). Every value shown comes from GET /complaints/{id}. */
export function ComplaintDetailView({ complaint, role, onReload, onReplace }: { complaint: ComplaintDetail; role: AccountRole; onReload: () => void; onReplace: (c: ComplaintDetail) => void }) {
  const c = complaint;
  const staff = role === "INVESTIGATOR" || role === "ADMIN";
  const loc = c.latitude !== null && c.longitude !== null ? `${c.latitude}, ${c.longitude}` : null;
  return (
    <div className="space-y-5">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <StatusChip status={c.status} />
          {c.latest_analysis && <RiskBadge level={c.latest_analysis.risk_level} score={c.latest_analysis.risk_score} assessment={c.latest_analysis.assessment} />}
          <span className="text-xs text-muted">{CATEGORY_LABEL[c.category] ?? c.category}</span>
        </div>
        <h1 className="text-2xl font-semibold tracking-tight" data-testid="complaint-title">
          {c.title}
        </h1>
        <p className="text-xs text-muted">
          Complaint ID <span className="mono text-fg" data-testid="complaint-id">{c.id}</span> · reported {formatDateTime(c.reported_at)} (set by the server)
        </p>
        <div className="flex flex-wrap items-start gap-2">
          <ReportDownload complaintId={c.id} staff={staff} />
          {!staff && c.status === "SUBMITTED" && <EditDetails complaint={c} onSaved={onReplace} />}
        </div>
      </header>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,400px)]">
        <div className="space-y-5">
          <Panel title="Complaint details" icon={<ClipboardList className="h-3.5 w-3.5" aria-hidden />} data-testid="complaint-fields">
            <div className="space-y-5 p-4">
              <section aria-label="Description" className="space-y-1.5">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-subtle">Description</h3>
                <p className="whitespace-pre-wrap text-sm">{c.description}</p>
              </section>
              <Group
                title="When"
                rows={[
                  ["Incident time", c.incident_at ? formatDateTime(c.incident_at) : <Value v={null} />],
                  ["Reported", formatDateTime(c.reported_at)],
                  ["Last updated", formatDateTime(c.updated_at)],
                ]}
              />
              <Group
                title="Money and payment"
                rows={[
                  ["Amount", c.amount ? formatAmount(c.amount, c.currency) : <Value v={null} />],
                  ["Payment method", <Value key="pm" v={c.payment_method ? PAYMENT_LABEL[c.payment_method] : null} />],
                  ["UPI ID", <Value key="u" v={c.upi_id} mono />],
                  ["UTR / reference", <Value key="r" v={c.utr_reference} mono />],
                  ["Bank name", <Value key="bn" v={c.bank_name} />],
                  ["Bank account", <Value key="ba" v={c.bank_account} mono />],
                  ["IFSC", <Value key="if" v={c.ifsc} mono />],
                ]}
              />
              <Group
                title="About the scammer"
                rows={[
                  ["Phone", <Value key="p" v={c.suspect_phone} mono />],
                  ["Email", <Value key="e" v={c.suspect_email} />],
                  ["Suspicious link", <Value key="l" v={c.suspicious_url} mono />],
                  ["Social platform", <Value key="sp" v={c.social_platform} />],
                  ["Social account", <Value key="sa" v={c.social_account} />],
                ]}
              />
              <Group
                title="Location"
                rows={[
                  ["Locality", <Value key="lt" v={c.location_text} />],
                  ["City", <Value key="c" v={c.city} />],
                  ["State", <Value key="s" v={c.state} />],
                  ["Coordinates", <Value key="co" v={loc} mono />],
                ]}
              />
              {staff && c.owner_user_id && <Group title="Staff" rows={[["Complainant user ID", <Value key="o" v={c.owner_user_id} mono />]]} />}
            </div>
          </Panel>
          <EvidenceManager complaintId={c.id} status={c.status} items={c.evidence} role={role} onChanged={onReload} />
          <AnalysisPanel complaintId={c.id} hasAnalysis={c.latest_analysis !== null} onRan={onReload} />
          {staff && <RelatedPanel complaintId={c.id} />}
          {staff && <SimilarPanel complaintId={c.id} />}
        </div>
        <div className="space-y-5">
          {staff && <StatusUpdate c={c} onUpdated={onReplace} />}
          {staff && <InvestigationNotes complaintId={c.id} />}
          {staff && <CaseTimeline complaintId={c.id} refreshKey={c.updated_at ?? ""} />}
          <Panel title="Status history" icon={<History className="h-3.5 w-3.5" aria-hidden />} data-testid="status-history">
            <ol className="relative space-y-4 p-4">
              {[...c.status_history].reverse().map((h, i) => (
                <li key={`${h.created_at}-${i}`} className="relative flex gap-3" data-testid="history-row">
                  <CheckCircle2 className={cn("mt-0.5 h-4 w-4 shrink-0", i === 0 ? "text-intel" : "text-subtle")} aria-hidden />
                  <div className="min-w-0 space-y-0.5">
                    <p className="text-sm">
                      {h.from_status ? (
                        <>
                          {STATUS_LABEL[h.from_status]} → <strong>{STATUS_LABEL[h.to_status]}</strong>
                        </>
                      ) : (
                        <strong>{STATUS_LABEL[h.to_status]}</strong>
                      )}
                    </p>
                    <p className="text-xs text-subtle">
                      {formatDateTime(h.created_at)}
                      {staff && h.actor_user_id ? ` · by ${shortId(h.actor_user_id)}` : ""}
                    </p>
                    {h.note && <p className="whitespace-pre-wrap rounded-lg border border-line bg-chip p-2 text-sm">{h.note}</p>}
                  </div>
                </li>
              ))}
            </ol>
          </Panel>
        </div>
      </div>
    </div>
  );
}
