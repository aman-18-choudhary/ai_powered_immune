import { useEffect, useState } from "react";
import { complaintsApi, messageFor } from "@/lib/complaints/api";
import type { EvidenceOcr, OcrText } from "@/lib/complaints/types";
import { Spinner } from "./ui";

const OCR_LABEL: Record<string, string> = {
  extracted: "Text read from the screenshot",
  no_text: "No readable text in the image",
  not_read_yet: "Not read yet",
  engine_unavailable: "Screenshot text not read",
  budget_exhausted: "Not read in the last analysis",
};

/** OCR state of one image file, with the service's precise reason (Phase 6). */
export function OcrLine({ o }: { o: EvidenceOcr }) {
  const ok = o.status === "extracted" || o.status === "no_text";
  return (
    <p className="mt-0.5 text-xs" data-testid="ocr-status" data-status={o.status}>
      <span className={ok ? "font-medium text-fg" : "font-medium text-warn"}>{OCR_LABEL[o.status] ?? "Screenshot text not read"}</span>
      <span className="text-muted">
        {" "}
        · {o.message}
        {typeof o.mean_confidence === "number" ? ` OCR confidence ${Math.round(o.mean_confidence)}/100.` : ""}
      </span>
    </p>
  );
}

/** The machine-read text of one screenshot, loaded on demand (owner and staff only). */
export function OcrViewer({ complaintId, evidenceId, id }: { complaintId: string; evidenceId: string; id: string }) {
  const [t, setT] = useState<OcrText | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const ac = new AbortController();
    setT(null);
    setError(null);
    complaintsApi
      .ocrText(complaintId, evidenceId, ac.signal)
      .then(setT)
      .catch((e: unknown) => {
        if (!ac.signal.aborted) setError(messageFor(e));
      });
    return () => ac.abort();
  }, [complaintId, evidenceId]);
  return (
    <div id={id} className="mt-2 rounded-lg border border-line bg-chip p-2 text-sm" data-testid="ocr-viewer">
      {error ? (
        <p role="alert" className="text-danger-text">
          {error}
        </p>
      ) : !t ? (
        <span className="inline-flex items-center gap-2 text-muted">
          <Spinner /> Loading the text
        </span>
      ) : (
        <>
          <p className="whitespace-pre-wrap" data-testid="ocr-text">
            {t.text}
          </p>
          <p className="mt-1 text-xs text-subtle">
            {t.note} {t.engine ? `Engine ${t.engine}` : ""}
            {t.languages.length ? `, languages ${t.languages.join(", ")}` : ""}.
          </p>
        </>
      )}
    </div>
  );
}
