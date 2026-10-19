import { useState, type FormEvent } from "react";
import { Download, NotebookPen, ScrollText } from "lucide-react";
import { EmptyState, Panel } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { ErrorBlock, LoadingBlock, Pager, Spinner, inputCls } from "./ui";
import { messageFor, saveBlob } from "@/lib/complaints/api";
import { caseApi, TIMELINE_LABEL } from "@/lib/complaints/caseTools";
import { formatDateTime, shortId } from "@/lib/complaints/format";
import { useResource } from "@/lib/complaints/useResource";
import { cn } from "@/lib/utils/cn";

const LIMIT = 20;
const MAX = 4000;

/** Staff only: working notes on a complaint. Never shown to the complainant; append-only. */
export function InvestigationNotes({ complaintId }: { complaintId: string }) {
  const [offset, setOffset] = useState(0);
  const res = useResource((s) => caseApi.notes(complaintId, { limit: LIMIT, offset }, s), `${complaintId}:${offset}`);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return setError("Write a note first.");
    setBusy(true);
    setError(null);
    try {
      await caseApi.addNote(complaintId, text);
      setText("");
      setAnnounce("Note saved.");
      setOffset(0);
      res.reload();
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Investigation notes" icon={<NotebookPen className="h-3.5 w-3.5" aria-hidden />} data-testid="notes-panel">
      <p className="border-b border-line px-4 py-2 text-xs text-muted">Visible to investigators and administrators only, never to the complainant. Notes cannot be edited or deleted.</p>
      <form onSubmit={(e) => void submit(e)} className="space-y-2 border-b border-line p-4" noValidate>
        <label htmlFor="note-text" className="block text-sm font-medium">
          New note
        </label>
        <textarea id="note-text" rows={3} maxLength={MAX} value={text} onChange={(e) => setText(e.target.value)} className={cn(inputCls, "resize-y")} disabled={busy} data-testid="note-text" />
        <div className="flex items-center justify-between">
          <span className="text-xs text-subtle">
            {text.length}/{MAX}
          </span>
          <Button type="submit" size="sm" variant="intel" disabled={busy} data-testid="note-save">
            {busy && <Spinner />} Save note
          </Button>
        </div>
        {error && (
          <p role="alert" className="text-sm text-danger-text" data-testid="note-error">
            {error}
          </p>
        )}
        <p role="status" aria-live="polite" className="sr-only">
          {announce}
        </p>
      </form>
      {res.loading && !res.data ? (
        <LoadingBlock label="Loading notes" />
      ) : res.error && !res.data ? (
        <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />
      ) : res.data && res.data.items.length === 0 ? (
        <EmptyState icon={<NotebookPen className="h-5 w-5" aria-hidden />} title="No notes yet" />
      ) : res.data ? (
        <>
          <ul className="divide-y divide-line" data-testid="note-list">
            {res.data.items.map((n) => (
              <li key={n.id} className="space-y-1 px-4 py-3" data-testid="note-item">
                <p className="whitespace-pre-wrap text-sm">{n.body}</p>
                <p className="text-xs text-subtle">
                  {formatDateTime(n.created_at)} · by <span className="mono">{shortId(n.author_user_id)}</span>
                </p>
              </li>
            ))}
          </ul>
          <Pager total={res.data.total} limit={LIMIT} offset={offset} onChange={setOffset} label="Notes" />
        </>
      ) : null}
    </Panel>
  );
}

/** Staff only: everything recorded about the complaint, in order. */
export function CaseTimeline({ complaintId, refreshKey }: { complaintId: string; refreshKey?: string }) {
  const res = useResource((s) => caseApi.timeline(complaintId, s), `${complaintId}:${refreshKey ?? ""}`);
  return (
    <Panel title="Case timeline" icon={<ScrollText className="h-3.5 w-3.5" aria-hidden />} actions={<Button size="sm" variant="ghost" onClick={res.reload}>Refresh</Button>} data-testid="timeline-panel">
      {res.loading && !res.data ? (
        <LoadingBlock label="Loading the timeline" />
      ) : res.error && !res.data ? (
        <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />
      ) : res.data ? (
        <ol className="space-y-3 p-4" data-testid="timeline-list">
          {res.data.items.map((x, i) => (
            <li key={`${x.at}-${i}`} className="grid grid-cols-[92px_minmax(0,1fr)] gap-2 text-sm" data-testid="timeline-item" data-kind={x.kind}>
              <span className="text-[11px] font-semibold uppercase tracking-wide text-subtle">{TIMELINE_LABEL[x.kind] ?? x.kind}</span>
              <span className="min-w-0 space-y-0.5">
                <span className="block">{x.text}</span>
                {x.note && <span className="block whitespace-pre-wrap rounded-lg border border-line bg-chip p-2 text-xs">{x.note}</span>}
                <span className="block text-xs text-subtle">
                  {formatDateTime(x.at)}
                  {x.actor_user_id ? ` · by ${shortId(x.actor_user_id)}` : ""}
                </span>
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </Panel>
  );
}

/** Owner or staff: download the case report (Markdown). The server records each export. */
export function ReportDownload({ complaintId, staff }: { complaintId: string; staff: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      saveBlob(await caseApi.report(complaintId), `complaint-${complaintId.slice(0, 8)}-report.md`);
    } catch (e) {
      setError(messageFor(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="inline-flex flex-col items-start gap-1">
      <Button size="sm" variant="secondary" onClick={() => void go()} disabled={busy} data-testid="report-download" title={staff ? "Staff version: includes investigation notes and link reviews" : "Your complaint, its status history, evidence list and analysis"}>
        {busy ? <Spinner /> : <Download className="h-3.5 w-3.5" aria-hidden />} Download report
      </Button>
      {error && (
        <span role="alert" className="text-xs text-danger-text" data-testid="report-error">
          {error}
        </span>
      )}
    </span>
  );
}
