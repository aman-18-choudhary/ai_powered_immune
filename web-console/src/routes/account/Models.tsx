import type { ReactNode } from "react";
import { AlertTriangle, AudioLines, BrainCircuit, CheckCircle2, CircleHelp, Cpu, Sparkles, XCircle } from "lucide-react";
import { Panel } from "@/components/common/Panel";
import { ErrorBlock, LoadingBlock, PageHeader } from "@/components/complaints/ui";
import { SourceBadge } from "@/components/complaints/AnalysisResultView";
import { complaintsApi } from "@/lib/complaints/api";
import { CATEGORY_LABEL, humanise } from "@/lib/complaints/format";
import type { Category, ModelEntry, ModelsInfo } from "@/lib/complaints/types";
import { useResource } from "@/lib/complaints/useResource";
import { usePageTitle } from "@/lib/hooks/usePageTitle";

const pct = (x: number | null | undefined) => (typeof x === "number" ? `${(x * 100).toFixed(1)}%` : "not reported");
const num = (x: number | null | undefined, d = 3) => (typeof x === "number" ? x.toFixed(d) : "not reported");

function Runtime({ status, reason }: { status: string; reason: string | null }) {
  const ok = status === "loaded" || status === "ready" || status === "available_offline";
  const partial = status === "fallback_rules_only";
  const Icon = ok ? CheckCircle2 : partial ? AlertTriangle : status === "not_configured" ? CircleHelp : XCircle;
  const cls = ok ? "text-safe border-safe/40 bg-safe-soft" : partial ? "text-warn border-warn/40 bg-warn-soft" : "text-muted border-line bg-chip";
  return (
    <span className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${cls}`} data-testid="runtime-status">
      <Icon className="h-3 w-3" aria-hidden />
      {humanise(status)}
      {reason ? ` (${humanise(reason).toLowerCase()})` : ""}
    </span>
  );
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 py-1.5 sm:grid-cols-[180px_1fr]">
      <dt className="text-xs text-subtle">{k}</dt>
      <dd className="min-w-0 break-words text-sm">{children}</dd>
    </div>
  );
}

function ModelCard({ m }: { m: ModelEntry }) {
  const per = m.metrics?.per_class ? Object.entries(m.metrics.per_class) : [];
  return (
    <Panel title={m.id} icon={<Cpu className="h-3.5 w-3.5" aria-hidden />} actions={<Runtime status={m.runtime.status} reason={m.runtime.reason} />} data-testid={`model-${m.id}`}>
      <div className="space-y-4 p-4">
        <dl className="divide-y divide-line">
          <Row k="Kind">
            {humanise(m.kind)} <SourceBadge source={m.source} className="ml-1" />
          </Row>
          <Row k="Version">
            <span className="mono">{m.version}</span>
          </Row>
          {m.algorithm && <Row k="Algorithm">{m.algorithm}</Row>}
          <Row k="Artifact SHA-256">
            <span className="mono break-all text-xs" data-testid="artifact-sha">
              {m.artifact.sha256 ?? "not available"}
            </span>
            {m.artifact.verified_at_load && <span className="block text-xs text-subtle">Checked at load: {m.artifact.verified_at_load}</span>}
          </Row>
          <Row k="Training data">
            <strong>{m.training_data.synthetic ? "Synthetic only. " : ""}</strong>
            {m.training_data.description}
            {m.training_data.rows ? ` (${m.training_data.rows.toLocaleString("en-IN")} rows)` : ""}
            {m.training_data.split && <span className="block text-xs text-subtle">{m.training_data.split}</span>}
          </Row>
        </dl>

        <section aria-labelledby={`${m.id}-metrics`}>
          <h3 id={`${m.id}-metrics`} className="text-xs font-semibold uppercase tracking-wide text-subtle">
            Held-out metrics (synthetic data)
          </h3>
          {m.metrics ? (
            <>
              <p className="mt-1 text-xs text-muted">{m.metrics.basis}. n = {m.metrics.n ?? "?"}.</p>
              <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="headline-metrics">
                {[
                  ["Macro-F1", num(m.metrics.macro_f1)],
                  ["Accuracy", num(m.metrics.accuracy)],
                  ["Abstention rate", pct(m.metrics.abstention_rate)],
                  ["Abstention threshold", num(m.metrics.abstention_threshold, 2)],
                ].map(([k, val]) => (
                  <div key={k} className="rounded-lg border border-line bg-chip p-2">
                    <dt className="text-[11px] text-subtle">{k}</dt>
                    <dd className="tnum text-base font-semibold">{val}</dd>
                  </div>
                ))}
              </dl>
              {typeof m.metrics.accuracy_on_answered === "number" && (
                <p className="mt-1 text-xs text-muted">Accuracy on the answers it did not abstain on: {num(m.metrics.accuracy_on_answered)}.</p>
              )}
              {per.length > 0 && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs text-muted">Per class</summary>
                  <div className="mt-1 overflow-x-auto">
                    <table className="w-full min-w-[420px] text-xs" data-testid="per-class">
                      <thead>
                        <tr className="text-left text-subtle">
                          <th className="py-1 pr-2 font-medium">Class</th>
                          <th className="py-1 pr-2 font-medium">Precision</th>
                          <th className="py-1 pr-2 font-medium">Recall</th>
                          <th className="py-1 pr-2 font-medium">F1</th>
                          <th className="py-1 font-medium">Support</th>
                        </tr>
                      </thead>
                      <tbody className="tnum">
                        {per.map(([c, r]) => (
                          <tr key={c} className="border-t border-line">
                            <td className="py-1 pr-2">{CATEGORY_LABEL[c as Category] ?? humanise(c)}</td>
                            <td className="py-1 pr-2">{num(r.precision, 2)}</td>
                            <td className="py-1 pr-2">{num(r.recall, 2)}</td>
                            <td className="py-1 pr-2">{num(r.f1, 2)}</td>
                            <td className="py-1">{r.support}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              )}
            </>
          ) : (
            <p className="mt-1 text-sm text-muted" data-testid="no-metrics">
              No held-out metrics are shown: {m.metrics_unavailable_reason ?? "none were shipped with this model"}.
            </p>
          )}
          {m.leaky_random_split && (
            <p className="mt-2 rounded-lg border border-line p-2 text-xs text-muted" data-testid="leaky-split">
              <strong>Not the headline:</strong> a leaky random split gives macro-F1 {num(m.leaky_random_split.macro_f1)}. {m.leaky_random_split.note}
            </p>
          )}
          {m.confidence_meaning && <p className="mt-2 text-xs text-muted">Confidence: {m.confidence_meaning}.</p>}
        </section>

        {m.limitations.length > 0 && (
          <section aria-labelledby={`${m.id}-lim`}>
            <h3 id={`${m.id}-lim`} className="text-xs font-semibold uppercase tracking-wide text-subtle">
              Known limitations
            </h3>
            <ul className="mt-1 list-disc space-y-1 pl-5 text-sm" data-testid="limitations">
              {m.limitations.map((l, n) => (
                <li key={n}>{l}</li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </Panel>
  );
}

function Llm({ llm }: { llm: ModelsInfo["llm"] }) {
  return (
    <Panel title="Local language model (explanations only)" icon={<Sparkles className="h-3.5 w-3.5" aria-hidden />} actions={<Runtime status={llm.status} reason={llm.reason} />} data-testid="model-llm">
      <div className="space-y-1 p-4 text-sm">
        <p>
          Provider: {llm.provider}
          {llm.model ? (
            <>
              {" "}
              · model <span className="mono">{llm.model}</span>
            </>
          ) : null}
          . Used for {llm.used_for}.
        </p>
        <p className="text-xs text-muted">
          Only a local server is ever contacted; no cloud AI service is used.
          {llm.status === "not_configured" && " None is configured on this server, so explanations are rule-based only."}
        </p>
      </div>
    </Panel>
  );
}

const STT_STATE: Record<string, string> = {
  ready: "The model is loaded.",
  available_offline: "The model is in this server's local cache and is loaded on first use.",
  model_not_available_offline: "The model is not in this server's local cache (it is never downloaded at runtime), so audio evidence is not transcribed.",
  engine_not_configured: "No speech-to-text engine is configured for evidence, so audio evidence is not transcribed.",
  configured: "A local whisper.cpp server is configured (not probed).",
};

function Stt({ stt }: { stt: NonNullable<ModelsInfo["stt"]> }) {
  return (
    <Panel title="Speech-to-text (audio evidence)" icon={<AudioLines className="h-3.5 w-3.5" aria-hidden />} actions={<Runtime status={stt.status} reason={stt.reason} />} data-testid="model-stt">
      <div className="space-y-1 p-4 text-sm">
        <p>
          Engine <span className="mono">{stt.engine}</span>
          {stt.model ? (
            <>
              {" "}
              · model <span className="mono">{stt.model}</span>
            </>
          ) : null}
          . Used for {stt.used_for}.
        </p>
        <p className="text-xs text-muted" data-testid="stt-state">
          {STT_STATE[stt.status] ?? humanise(stt.status)} Runs on this server only.
          {stt.evidence_transcription === "disabled" && " Evidence transcription is turned off in the configuration."}
          {stt.limits && ` Limits per analysis: ${stt.limits.max_files_per_run} new file(s), ${stt.limits.max_seconds_per_file} s per file, ${stt.limits.run_budget_s} s in total.`}
          {stt.decoders?.mp3_ogg_m4a === "unavailable" && " MP3, OGG and M4A cannot be decoded here (PyAV is not installed); WAV can."}
        </p>
      </div>
    </Panel>
  );
}

export default function Models() {
  usePageTitle("Models");
  const res = useResource((s) => complaintsApi.models(s), "models");
  return (
    <div className="mx-auto max-w-5xl space-y-5 px-4 py-6 md:px-6">
      <PageHeader title="Models" subtitle="The trained models behind the complaint analysis and the message check, as reported by the service." />
      {res.loading && !res.data ? (
        <LoadingBlock label="Loading models" />
      ) : res.error && !res.data ? (
        <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />
      ) : res.data ? (
        <>
          <section role="note" aria-label="Synthetic data warning" className="flex items-start gap-3 rounded-xl border border-warn/50 bg-warn-soft p-4" data-testid="synthetic-warning">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-warn" aria-hidden />
            <div className="space-y-1 text-sm">
              <p className="font-semibold">Trained and evaluated on synthetic data only</p>
              <p>{res.data.synthetic_data_warning}</p>
              <p className="text-xs text-muted">The risk score is an uncalibrated heuristic, not a probability; model confidences are uncalibrated too.</p>
            </div>
          </section>
          {res.data.models.map((m) => (
            <ModelCard key={m.id} m={m} />
          ))}
          <Llm llm={res.data.llm} />
          {res.data.stt && <Stt stt={res.data.stt} />}
          <p className="flex items-center gap-1.5 text-xs text-subtle">
            <BrainCircuit className="h-3.5 w-3.5" aria-hidden /> Rule components (call-guard cue rules, link and format checks) are not trained models and are not listed here.
          </p>
        </>
      ) : null}
    </div>
  );
}
