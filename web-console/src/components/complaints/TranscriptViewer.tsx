import { useEffect, useState } from "react";
import { complaintsApi, messageFor } from "@/lib/complaints/api";
import { span } from "@/lib/complaints/transcription";
import type { Transcript } from "@/lib/complaints/types";
import { Spinner } from "./ui";

/**
 * The stored transcript of one audio evidence file with segment timestamps, loaded from the
 * service on demand (owner and staff only; the service enforces access).
 */
export function TranscriptViewer({ complaintId, evidenceId, id }: { complaintId: string; evidenceId: string; id: string }) {
  const [t, setT] = useState<Transcript | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    setT(null);
    setError(null);
    complaintsApi
      .transcript(complaintId, evidenceId, ac.signal)
      .then(setT)
      .catch((err: unknown) => {
        if (!ac.signal.aborted) setError(messageFor(err));
      });
    return () => ac.abort();
  }, [complaintId, evidenceId]);

  if (error) {
    return (
      <p id={id} role="alert" className="mt-2 rounded-lg border border-danger/40 bg-danger-soft p-2 text-xs" data-testid="transcript-error">
        {error}
      </p>
    );
  }
  if (!t) {
    return (
      <p id={id} className="mt-2 flex items-center gap-1.5 text-xs text-muted" role="status">
        <Spinner /> Loading transcript…
      </p>
    );
  }
  return (
    <div id={id} className="mt-2 rounded-lg border border-line bg-chip p-2 text-xs" data-testid="transcript">
      <p className="text-subtle">
        {t.engine && t.model ? (
          <>
            Machine transcript (<span className="mono">{t.engine}:{t.model}</span>
            {t.language ? `, language ${t.language}` : ""}).{" "}
          </>
        ) : null}
        {t.note}
        {t.truncated && ` Only the first ${t.transcribed_s ?? "?"} s of ${t.duration_s ?? "?"} s were transcribed.`}
      </p>
      {t.segments.length === 0 ? (
        <p className="mt-1 text-muted" data-testid="transcript-empty">
          No speech was recognised in this recording.
        </p>
      ) : (
        <ol className="mt-1.5 space-y-1" aria-label="Transcript segments">
          {t.segments.map((s, n) => (
            <li key={n} className="flex gap-2" data-testid="transcript-segment">
              <span className="mono tnum shrink-0 text-subtle">{span(s.start_s, s.end_s)}</span>
              <span className="text-fg">{s.text}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
