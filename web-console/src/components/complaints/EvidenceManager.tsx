import { useRef, useState } from "react";
import { Download, FileAudio, FileImage, FileText, Paperclip, Trash2, Upload } from "lucide-react";
import { EmptyState, Panel } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Spinner } from "./ui";
import { UploadList } from "./UploadList";
import { complaintsApi, messageFor, saveBlob } from "@/lib/complaints/api";
import { ACCEPT, MAX_FILES } from "@/lib/complaints/form";
import { formatBytes, formatDateTime } from "@/lib/complaints/format";
import type { AccountRole, ComplaintStatus, Evidence } from "@/lib/complaints/types";
import { uploadSequential, type UploadItem } from "@/lib/complaints/upload";

function iconFor(mime: string) {
  if (mime.startsWith("audio/")) return FileAudio;
  if (mime.startsWith("image/")) return FileImage;
  return FileText;
}

/**
 * Evidence list with authorised download, removal (owner while SUBMITTED; administrator any time)
 * and adding files (owner, while the complaint is open). The service enforces every rule.
 */
export function EvidenceManager({
  complaintId,
  status,
  items,
  role,
  onChanged,
}: {
  complaintId: string;
  status: ComplaintStatus;
  items: Evidence[];
  role: AccountRole;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Evidence | null>(null);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const owner = role === "CITIZEN";
  const canDelete = (owner && status === "SUBMITTED") || role === "ADMIN";
  const canUpload = owner && status !== "RESOLVED" && status !== "REJECTED";

  const download = async (e: Evidence) => {
    setBusy(e.id);
    setError(null);
    try {
      const blob = await complaintsApi.downloadEvidence(complaintId, e.id);
      saveBlob(blob, e.original_filename);
      setAnnounce(`Downloaded ${e.original_filename}.`);
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (e: Evidence) => {
    setBusy(e.id);
    setError(null);
    try {
      const r = await complaintsApi.deleteEvidence(complaintId, e.id);
      setAnnounce(r.file_removed ? `${e.original_filename} was removed.` : `${e.original_filename} was removed from the complaint, but the stored file could not be deleted; it has been logged for clean-up.`);
      setConfirm(null);
      onChanged();
    } catch (err) {
      setError(messageFor(err));
      setConfirm(null);
    } finally {
      setBusy(null);
    }
  };

  const upload = async (list: FileList | null) => {
    if (!list || list.length === 0) return;
    const files = Array.from(list).slice(0, Math.max(0, MAX_FILES - items.length));
    if (fileRef.current) fileRef.current.value = "";
    if (files.length === 0) {
      setError("This complaint already has the maximum number of evidence files.");
      return;
    }
    setUploading(true);
    setError(null);
    await uploadSequential(complaintId, files, setUploads);
    setUploading(false);
    onChanged();
  };

  return (
    <Panel
      title={`Evidence (${items.length})`}
      icon={<Paperclip className="h-3.5 w-3.5" aria-hidden />}
      actions={
        canUpload ? (
          <>
            <label htmlFor={`ev-${complaintId}`} className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-xl border border-line-strong bg-chip px-3 text-xs font-medium hover:bg-hover">
              {uploading ? <Spinner /> : <Upload className="h-3.5 w-3.5" aria-hidden />} Add evidence
            </label>
            <input id={`ev-${complaintId}`} ref={fileRef} type="file" multiple accept={ACCEPT} className="sr-only" disabled={uploading} onChange={(e) => void upload(e.target.files)} data-testid="add-evidence" />
          </>
        ) : undefined
      }
      data-testid="evidence-panel"
    >
      <p className="sr-only" role="status" aria-live="polite">
        {announce}
      </p>
      {error && (
        <p role="alert" className="mx-4 mt-3 rounded-lg border border-danger/40 bg-danger-soft p-2 text-sm" data-testid="evidence-error">
          {error}
        </p>
      )}
      {uploads.length > 0 && (
        <div className="px-4 pt-3">
          <UploadList items={uploads} />
        </div>
      )}
      {items.length === 0 ? (
        <EmptyState icon={<Paperclip className="h-5 w-5" aria-hidden />} title="No evidence files" hint={canUpload ? "Add screenshots, PDFs or call recordings if you have them." : undefined} />
      ) : (
        <ul className="divide-y divide-line">
          {items.map((e) => {
            const Icon = iconFor(e.mime_type);
            return (
              <li key={e.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5" data-testid="evidence-item">
                <Icon className="h-4 w-4 shrink-0 text-muted" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{e.original_filename}</p>
                  <p className="text-xs text-subtle">
                    {e.mime_type} · {formatBytes(e.size_bytes)} · added {formatDateTime(e.created_at)} · <span className="mono" title={e.sha256}>sha256 {e.sha256.slice(0, 12)}…</span>
                  </p>
                </div>
                <div className="flex gap-1.5">
                  <Button size="sm" variant="secondary" onClick={() => void download(e)} disabled={busy === e.id} aria-label={`Download ${e.original_filename}`} data-testid="evidence-download">
                    {busy === e.id ? <Spinner /> : <Download className="h-3.5 w-3.5" aria-hidden />} Download
                  </Button>
                  {canDelete && (
                    <Button size="sm" variant="danger" onClick={() => setConfirm(e)} disabled={busy === e.id} aria-label={`Remove ${e.original_filename}`} data-testid="evidence-delete">
                      <Trash2 className="h-3.5 w-3.5" aria-hidden /> Remove
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {owner && !canDelete && items.length > 0 && (
        <p className="px-4 pb-3 text-xs text-subtle">Files can be removed only before the complaint is picked up for review.</p>
      )}
      <Dialog open={confirm !== null} onOpenChange={(o) => !o && setConfirm(null)}>
        <DialogContent className="w-[min(94vw,440px)]">
          <DialogTitle className="text-lg font-semibold">Remove this file?</DialogTitle>
          <DialogDescription className="mt-2 text-sm text-muted">
            {confirm?.original_filename} will be removed from the complaint and the stored file deleted. This cannot be undone.
            {role === "ADMIN" && " The complainant will be told that an administrator removed it."}
          </DialogDescription>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={() => confirm && void remove(confirm)} disabled={busy !== null} data-testid="evidence-delete-confirm">
              {busy ? <Spinner /> : <Trash2 className="h-4 w-4" aria-hidden />} Remove file
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </Panel>
  );
}
