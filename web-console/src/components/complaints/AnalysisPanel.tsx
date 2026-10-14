import { useState } from "react";
import { Gauge, Play, RefreshCw } from "lucide-react";
import { Panel } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { ErrorBlock, LoadingBlock, RiskBadge, Spinner } from "./ui";
import { ComplaintsApiError, complaintsApi, messageFor } from "@/lib/complaints/api";
import { CATEGORY_LABEL, formatDateTime, humanise } from "@/lib/complaints/format";
import type { Analysis, Category } from "@/lib/complaints/types";
import { useResource } from "@/lib/complaints/useResource";

const SOURCE_LABEL: Record<string, string> = { rules: "rule-based (deterministic)", ml: "machine-learning model", llm: "local language model" };
const IND_SOURCE: Record<string, string> = { call_guard: "rules + ML (call-guard)", url_heuristics: "URL heuristics", entity_format_checks: "format checks" };

function AnalysisBody({ a }: { a: Analysis }) {
  const unavailable = Object.entries(a.capabilities).filter(([, s]) => s === "unavailable" || s === "timeout" || s === "error");
  return (
    <div className="space-y-4 p-4 text-sm" data-testid="analysis">
      <div className="flex flex-wrap items-center gap-2">
        <RiskBadge level={a.risk_level} score={a.risk_score} />
        <span className="rounded-md border border-line bg-chip px-1.5 py-0.5 text-[11px]">Status: {humanise(a.status)}</span>
        {a.classification && (
          <span className="rounded-md border border-line bg-chip px-1.5 py-0.5 text-[11px]">
            Category: {CATEGORY_LABEL[a.classification as Category] ?? humanise(a.classification)}
            {typeof a.confidence === "number" ? ` (model score ${a.confidence.toFixed(2)}, uncalibrated)` : ""}
          </span>
        )}
      </div>
      <p className="text-xs text-subtle" data-testid="score-semantics">
        Risk score {a.risk_score ?? "not available"}
        {a.risk_score !== null ? "/100" : ""}: an uncalibrated heuristic score, not a probability.
      </p>
      {a.explanation && (
        <section aria-labelledby="an-expl">
          <h3 id="an-expl" className="text-xs font-semibold uppercase tracking-wide text-subtle">
            Explanation <span className="normal-case tracking-normal">· source: {SOURCE_LABEL[a.explanation_source ?? ""] ?? a.explanation_source ?? "not stated"}</span>
          </h3>
          <p className="mt-1">{a.explanation}</p>
          {a.llm_explanation && (
            <p className="mt-2 rounded-lg border border-line bg-chip p-2 text-muted">
              <span className="text-xs font-semibold">Additional wording (local language model; does not change the score): </span>
              {a.llm_explanation}
            </p>
          )}
        </section>
      )}
      <section aria-labelledby="an-ind">
        <h3 id="an-ind" className="text-xs font-semibold uppercase tracking-wide text-subtle">
          Indicators
        </h3>
        {a.indicators.length === 0 ? (
          <p className="mt-1 text-muted">No indicators were found.</p>
        ) : (
          <ul className="mt-1 space-y-1">
            {a.indicators.map((i, n) => (
              <li key={`${i.code}-${n}`} className="flex flex-wrap items-baseline gap-x-2">
                <span>{i.text}</span>
                <span className="text-xs text-subtle">
                  {IND_SOURCE[i.source] ?? i.source} · weight {i.weight}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
      {a.recommended_actions.length > 0 && (
        <section aria-labelledby="an-act">
          <h3 id="an-act" className="text-xs font-semibold uppercase tracking-wide text-subtle">
            Recommended actions <span className="normal-case tracking-normal">· general guidance, not a finding about this complaint</span>
          </h3>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {a.recommended_actions.map((r, n) => (
              <li key={n}>{r.text}</li>
            ))}
          </ul>
        </section>
      )}
      {unavailable.length > 0 && (
        <section aria-labelledby="an-un">
          <h3 id="an-un" className="text-xs font-semibold uppercase tracking-wide text-subtle">
            Not analysed (capability unavailable)
          </h3>
          <ul className="mt-1 flex flex-wrap gap-1.5" data-testid="unavailable-capabilities">
            {unavailable.map(([k, s]) => (
              <li key={k} className="rounded-md border border-line bg-chip px-1.5 py-0.5 text-[11px] text-muted" title={a.capability_reasons[k] ? humanise(a.capability_reasons[k]!) : undefined}>
                {humanise(k)}: {s}
                {a.capability_reasons[k] ? ` (${humanise(a.capability_reasons[k]!).toLowerCase()})` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="text-xs text-subtle">
        Version {a.version} · {formatDateTime(a.created_at)} · model <span className="mono">{a.pipeline_version}</span>
      </p>
      {a.limitations.length > 0 && (
        <details className="text-xs text-muted">
          <summary className="cursor-pointer">Limitations</summary>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {a.limitations.map((l, n) => (
              <li key={n}>{l}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

/** Latest analysis (GET .../analysis) and "Run analysis" (POST .../analysis). */
export function AnalysisPanel({ complaintId, hasAnalysis, onRan }: { complaintId: string; hasAnalysis: boolean; onRan?: () => void }) {
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
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
      setAnnounce(`Analysis finished: ${a.risk_level ? `${a.risk_level.toLowerCase()} risk` : "risk not scored"}.`);
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
        <AnalysisBody a={res.data} />
      ) : (
        <p className="p-4 text-sm text-muted" data-testid="analysis-none">
          No analysis has been run for this complaint yet. Running one checks the text and any link with rules and models that run on this server; it
          does not look anything up on the internet.
        </p>
      )}
    </Panel>
  );
}
