import { useState } from "react";
import { Gauge, History, Play, RefreshCw } from "lucide-react";
import { Panel } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { ErrorBlock, LoadingBlock, Pager, RiskBadge, Spinner } from "./ui";
import { AnalysisResultView } from "./AnalysisResultView";
import { ComplaintsApiError, complaintsApi, messageFor } from "@/lib/complaints/api";
import { formatDateTime, humanise, resultText } from "@/lib/complaints/format";
import { useResource } from "@/lib/complaints/useResource";

function VersionHistory({ complaintId, nonce }: { complaintId: string; nonce: number }) {
  const [offset, setOffset] = useState(0);
  const limit = 10;
  const res = useResource((sig) => complaintsApi.analysisVersions(complaintId, { limit, offset }, sig), `${complaintId}:${offset}:${nonce}`);
  return (
    <section aria-labelledby="an-versions" className="border-t border-line" data-testid="analysis-versions">
      <h3 id="an-versions" className="flex items-center gap-1.5 px-4 pt-3 text-xs font-semibold uppercase tracking-wide text-subtle">
        <History className="h-3.5 w-3.5" aria-hidden /> Analysis versions
      </h3>
      {res.loading && !res.data ? (
        <p className="px-4 py-2 text-xs text-muted" role="status">
          Loading versions…
        </p>
      ) : res.error ? (
        <p className="px-4 py-2 text-xs text-danger-text" role="alert">
          Versions could not be loaded. {res.error}
        </p>
      ) : res.data && res.data.items.length > 0 ? (
        <>
          <ol className="divide-y divide-line px-4">
            {res.data.items.map((v) => (
              <li key={v.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-xs" data-testid="analysis-version">
                <span className="font-medium">v{v.version}</span>
                <RiskBadge level={v.risk_level} score={v.risk_score} assessment={v.assessment} />
                <span className="text-muted">{humanise(v.status)}</span>
                <span className="text-subtle">{formatDateTime(v.created_at)}</span>
                <span className="mono text-subtle">{v.pipeline_version}</span>
                {v.provenance_derived_from_legacy && <span className="text-subtle">(older format)</span>}
              </li>
            ))}
          </ol>
          <Pager total={res.data.total} limit={limit} offset={offset} onChange={setOffset} label="Analysis versions" />
        </>
      ) : (
        <p className="px-4 py-2 text-xs text-muted">No versions yet.</p>
      )}
    </section>
  );
}

/** Latest analysis (GET .../analysis) and "Run analysis" (POST .../analysis). */
export function AnalysisPanel({ complaintId, hasAnalysis, onRan }: { complaintId: string; hasAnalysis: boolean; onRan?: () => void }) {
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const [nonce, setNonce] = useState(0);
  // the complaint detail says whether an analysis exists; only then is the full result fetched
  const res = useResource(async (s) => {
    if (!hasAnalysis) return null;
    try {
      return await complaintsApi.analysis(complaintId, s);
    } catch (e) {
      if (e instanceof ComplaintsApiError && e.status === 404 && e.serverCode === "analysis_not_found") return null;
      throw e;
    }
  }, `${complaintId}:${hasAnalysis}`);

  const run = async () => {
    setRunning(true);
    setRunError(null);
    setAnnounce("Running analysis…");
    try {
      const a = await complaintsApi.runAnalysis(complaintId);
      res.setData(a);
      setNonce((n) => n + 1);
      setAnnounce(`Analysis finished: ${resultText(a.risk_level, a.risk_score, a.assessment)}.`);
      onRan?.();
    } catch (e) {
      setRunError(`The analysis could not be run. ${messageFor(e)}`);
      setAnnounce("");
    } finally {
      setRunning(false);
    }
  };

  return (
    <Panel
      title="Analysis"
      icon={<Gauge className="h-3.5 w-3.5" aria-hidden />}
      actions={
        <Button size="sm" variant="intel" onClick={() => void run()} disabled={running} data-testid="run-analysis">
          {running ? <Spinner /> : res.data ? <RefreshCw className="h-3.5 w-3.5" aria-hidden /> : <Play className="h-3.5 w-3.5" aria-hidden />}
          {res.data ? "Run again" : "Run analysis"}
        </Button>
      }
      data-testid="analysis-panel"
    >
      <p className="sr-only" role="status" aria-live="polite">
        {announce}
      </p>
      {runError && (
        <p role="alert" className="mx-4 mt-3 rounded-lg border border-danger/40 bg-danger-soft p-2 text-sm" data-testid="analysis-error">
          {runError}
        </p>
      )}
      {res.loading && res.data === null && !res.error ? (
        <LoadingBlock label="Loading analysis" />
      ) : res.error ? (
        <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />
      ) : res.data ? (
        <div className="p-4">
          <AnalysisResultView a={res.data} footer={<p className="text-xs text-subtle">Version {res.data.version}</p>} />
        </div>
      ) : (
        <p className="p-4 text-sm text-muted" data-testid="analysis-none">
          No analysis has been run for this complaint yet. Running one checks the text and any link with rules and models that run on this server; it
          does not look anything up on the internet.
        </p>
      )}
      {(res.data || hasAnalysis) && <VersionHistory complaintId={complaintId} nonce={nonce} />}
    </Panel>
  );
}
