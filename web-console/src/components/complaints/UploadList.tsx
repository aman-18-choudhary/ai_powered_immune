import { CheckCircle2, CircleDashed, Loader2, XCircle, AlertTriangle } from "lucide-react";
import { formatBytes } from "@/lib/complaints/format";
import type { UploadItem } from "@/lib/complaints/upload";
import { cn } from "@/lib/utils/cn";

const LABEL = { pending: "Waiting", uploading: "Uploading", stored: "Stored", rejected: "Rejected", failed: "Failed" } as const;

/** Per-file upload status; "Stored" appears only for files the service confirmed. */
export function UploadList({ items }: { items: UploadItem[] }) {
  if (items.length === 0) return null;
  const done = items.filter((i) => i.status === "stored").length;
  const finished = items.every((i) => i.status !== "pending" && i.status !== "uploading");
  return (
    <div className="space-y-2" data-testid="upload-list">
      <p role="status" aria-live="polite" className="text-xs text-muted" data-testid="upload-summary">
        {finished ? `${done} of ${items.length} ${items.length === 1 ? "file" : "files"} stored.` : `Uploading ${items.length} ${items.length === 1 ? "file" : "files"}…`}
      </p>
      <ul className="space-y-1.5">
        {items.map((it) => (
          <li
            key={it.key}
            className={cn(
              "flex items-start gap-2 rounded-lg border px-3 py-2 text-sm",
              it.status === "stored" ? "border-safe/40 bg-safe-soft" : it.status === "rejected" || it.status === "failed" ? "border-danger/40 bg-danger-soft" : "border-line bg-chip",
            )}
            data-testid="upload-item"
            data-status={it.status}
          >
            {it.status === "stored" ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-safe" aria-hidden />
            ) : it.status === "uploading" ? (
              <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-intel" aria-hidden />
            ) : it.status === "rejected" ? (
              <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger-text" aria-hidden />
            ) : it.status === "failed" ? (
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger-text" aria-hidden />
            ) : (
              <CircleDashed className="mt-0.5 h-4 w-4 shrink-0 text-subtle" aria-hidden />
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate">
                <span className="font-medium">{it.name}</span> <span className="text-xs text-subtle">({formatBytes(it.size)})</span>
              </p>
              <p className="text-xs">
                <strong>{LABEL[it.status]}</strong>
                {it.reason ? `: ${it.reason}` : ""}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
