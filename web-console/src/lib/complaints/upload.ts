import { ApiError } from "@/lib/api/client";
import { complaintsApi, messageFor } from "./api";
import { precheckFile } from "./form";
import type { Evidence } from "./types";
import { isRecord } from "@/types/guards";

export type UploadStatus = "pending" | "uploading" | "stored" | "rejected" | "failed";

export interface UploadItem {
  key: string;
  name: string;
  size: number;
  status: UploadStatus;
  /** Why it was rejected or failed (the service's own message when it sent one). */
  reason?: string;
  /** Set only when the service answered with a stored evidence record. */
  evidence?: Evidence;
}

export function toItems(files: File[]): UploadItem[] {
  return files.map((f, i) => ({ key: `${i}:${f.name}:${f.size}:${f.lastModified}`, name: f.name, size: f.size, status: "pending" }));
}

function isStoredEvidence(v: unknown): v is Evidence {
  return isRecord(v) && typeof v.id === "string" && v.status === "stored" && typeof v.sha256 === "string";
}

/**
 * Upload files one by one. A file counts as stored ONLY when the service returned an evidence
 * record with status "stored"; a 4xx is "rejected" with its reason, anything else "failed".
 */
export async function uploadSequential(complaintId: string, files: File[], onChange: (items: UploadItem[]) => void): Promise<UploadItem[]> {
  const items = toItems(files);
  const emit = () => onChange(items.map((x) => ({ ...x })));
  emit();
  for (let i = 0; i < files.length; i++) {
    const f = files[i]!;
    const item = items[i]!;
    const pre = precheckFile(f);
    if (pre) {
      item.status = "rejected";
      item.reason = `${pre} Not sent.`;
      emit();
      continue;
    }
    item.status = "uploading";
    emit();
    try {
      const ev: unknown = await complaintsApi.uploadEvidence(complaintId, f);
      if (isStoredEvidence(ev)) {
        item.status = "stored";
        item.evidence = ev;
      } else {
        item.status = "failed";
        item.reason = "The service did not confirm that the file was stored.";
      }
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      item.status = status >= 400 && status < 500 && status !== 429 && status !== 401 ? "rejected" : "failed";
      item.reason = messageFor(e);
    }
    emit();
  }
  return items;
}
