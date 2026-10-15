import type { ReactNode } from "react";
import { BookOpen, Cpu, Gauge, History, ListChecks, Sparkles, type LucideIcon } from "lucide-react";
import { RiskBadge } from "./ui";
import { CATEGORY_LABEL, formatDateTime, humanise } from "@/lib/complaints/format";
import type { AnalysisResult, Category, ExplanationPart, Indicator, ProvenanceEvidence } from "@/lib/complaints/types";
import { cn } from "@/lib/utils/cn";

/** Provenance sources (services/complaints/complaints/provenance.py) as icon + words, never colour alone. */
const SOURCE: Record<string, { label: string; long: string; Icon: LucideIcon; cls: string }> = {
  // rule: text in the body colour (cyan text on the cyan tint is below 4.5:1 in the light theme)
  rule: { label: "Rule", long: "Rules (deterministic)", Icon: ListChecks, cls: "border-flow/40 bg-flow-soft text-fg [&>svg]:text-flow" },
  ml: { label: "ML model", long: "ML models (trained on synthetic data)", Icon: Cpu, cls: "border-intel/40 bg-intel-soft text-intel" },
  llm: { label: "LLM (local)", long: "LLM explanation (local model, wording only)", Icon: Sparkles, cls: "border-warn/40 bg-warn-soft text-warn" },
  static_guidance: { label: "Static guidance", long: "Static guidance", Icon: BookOpen, cls: "border-line bg-chip text-muted" },
  legacy: { label: "Legacy", long: "Older analysis (source not recorded)", Icon: History, cls: "border-line bg-chip text-muted" },
};
const GROUPS = ["rule", "ml", "llm"] as const;

/** Group of an indicator; results from before provenance carried the old stage name as source. */
export function groupOf(i: Indicator): (typeof GROUPS)[number] {
  if (i.source === "rule" || i.source === "ml" || i.source === "llm") return i.source;
  if (i.source === "call_guard") return i.code === "SCRIPT_CLASSIFIER_MATCH" ? "ml" : "rule";
  return "rule";
}

export function SourceBadge({ source, className }: { source: string; className?: string }) {
  const s = SOURCE[source] ?? SOURCE.legacy!;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium", s.cls, className)} data-testid="source-badge" data-source={source}>
      <s.Icon className="h-3 w-3" aria-hidden />
      {s.label}
    </span>
  );
}

const STATUS_TEXT: Record<string, string> = {
  COMPLETED: "Completed",
  PARTIAL: "Partial: some checks were unavailable",
  UNAVAILABLE: "Unavailable: nothing could be scored",
  INSUFFICIENT_INPUT: "Insufficient input: the text is too short to analyse",
};

function EvidenceNote({ ev }: { ev?: ProvenanceEvidence }) {
  if (!ev) return null;
  let body: ReactNode;
  if (ev.type === "text_span" && ev.snippet) {
    body = (
      <>
        in {ev.field ?? "the text"}
        {typeof ev.start === "number" ? ` (characters ${ev.start}–${ev.end})` : ""}: <q className="text-fg">{ev.snippet}</q>
      </>
    );
  } else if (ev.type === "text_span") body = <>in {ev.field ?? "the text"} (span not located)</>;
  else if (ev.type === "field") body = <>from the field {ev.field}{ev.host ? ` (host ${ev.host})` : ""}</>;
  else if (ev.type === "whole_text") body = <>scores the whole text, not a span</>;
  else if (ev.type === "not_recorded") body = <>evidence not recorded (older analysis)</>;
  else return null;
  return (
    <span className="block text-xs text-subtle" data-testid="evidence">
      Evidence: {body}
    </span>
  );
}

function IndicatorItem({ i }: { i: Indicator }) {
  return (
    <li className="space-y-0.5" data-testid="indicator">
      <span className="flex flex-wrap items-baseline gap-x-2">
        <span>{i.text}</span>
        <span className="text-xs text-subtle">
          <span className="mono">{i.method ?? i.stage ?? i.source}</span> · weight {i.weight}
          {(i.derived_from_legacy || !i.method) && " · older analysis"}
        </span>
      </span>
      <EvidenceNote ev={i.evidence} />
    </li>
  );
}

function sectionHead(id: string, children: ReactNode) {
  return (
    <h3 id={id} className="text-xs font-semibold uppercase tracking-wide text-subtle">
      {children}
    </h3>
  );
}

/**
 * One analysis (stored or a stateless check): uncalibrated score, level, category with its
 * confidence interpretation, indicators grouped by source with method and evidence, explanation
 * parts with their source, guidance, unavailable capabilities and provenance.
 */
export function AnalysisResultView({ a, idPrefix = "an", footer }: { a: AnalysisResult; idPrefix?: string; footer?: ReactNode }) {
  const unavailable = Object.entries(a.capabilities).filter(([, s]) => s === "unavailable" || s === "timeout" || s === "error");
  const cm = a.category_model;
  const top = cm?.top_categories ?? cm?.top ?? [];
  const threshold = cm?.abstention_threshold ?? cm?.min_confidence;
  const parts: ExplanationPart[] = a.explanation_parts?.length
    ? a.explanation_parts
    : a.explanation
      ? [{ text: a.explanation, source: a.explanation_source === "rules" ? "rule" : "legacy", method: "explanation_template" }]
      : [];
  if (a.llm_explanation && !parts.some((p) => p.source === "llm")) parts.push({ text: a.llm_explanation, source: "llm", method: `ollama:${a.llm_model ?? "local"}` });
  const grouped = GROUPS.map((g) => ({ g, items: a.indicators.filter((i) => groupOf(i) === g) }));
  const llmParts = parts.filter((p) => p.source === "llm");
  const prov = a.provenance;
  const versions = Object.entries(prov?.model_versions ?? {}).filter(([, v]) => v);
  const id = (s: string) => `${idPrefix}-${s}`;

  return (
    <div className="space-y-4 text-sm" data-testid="analysis">
      <div className="flex flex-wrap items-center gap-2">
        <RiskBadge level={a.risk_level} score={a.risk_score} />
        <span className="rounded-md border border-line bg-chip px-1.5 py-0.5 text-[11px]" data-testid="analysis-status">
          {STATUS_TEXT[a.status] ?? humanise(a.status)}
        </span>
      </div>
      <p className="flex items-start gap-1.5 text-xs text-subtle" data-testid="score-semantics">
        <Gauge className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        <span>
          Risk score {a.risk_score ?? "not given"}
          {a.risk_score !== null ? "/100" : ""}: an uncalibrated heuristic score, not a probability.
          {a.risk_level === null && " No level is shown when a core check did not run or the input was too short; that is not a finding that it is safe."}
        </span>
      </p>

      <section aria-labelledby={id("cat")} className="space-y-1" data-testid="category">
        {sectionHead(id("cat"), <>Predicted category <SourceBadge source="ml" className="ml-1 align-middle normal-case tracking-normal" /></>)}
        {a.classification ? (
          <p>
            <strong>{a.classification === "UNDETERMINED" ? "Undetermined" : (CATEGORY_LABEL[a.classification as Category] ?? humanise(a.classification))}</strong>
            {typeof a.confidence === "number" && <span className="text-muted"> · confidence {a.confidence.toFixed(2)} (uncalibrated)</span>}
          </p>
        ) : (
          <p className="text-muted">No category was predicted.</p>
        )}
        {a.confidence_interpretation && (
          <p className="text-xs text-muted" data-testid="confidence-interpretation">
            {a.confidence_interpretation}
          </p>
        )}
        {top.length > 0 && (
          <ol className="flex flex-wrap gap-1.5 text-[11px]" aria-label="Top categories with model scores" data-testid="top-categories">
            {top.slice(0, 3).map((t) => (
              <li key={t.category} className="rounded-md border border-line bg-chip px-1.5 py-0.5">
                {CATEGORY_LABEL[t.category as Category] ?? humanise(t.category)} <span className="tnum text-subtle">{t.score.toFixed(2)}</span>
              </li>
            ))}
            {typeof threshold === "number" && <li className="px-1 py-0.5 text-subtle">abstains below {threshold}</li>}
          </ol>
        )}
      </section>

      <section aria-labelledby={id("ind")} className="space-y-2">
        {sectionHead(id("ind"), "Indicators by source")}
        {GROUPS.map((g) => {
          const items = grouped.find((x) => x.g === g)!.items;
          const s = SOURCE[g]!;
          return (
            <div key={g} className="rounded-lg border border-line p-2.5" data-testid={`indicator-group-${g}`}>
              <h4 className="flex items-center gap-2 text-xs font-medium">
                <SourceBadge source={g} /> <span className="text-muted">{s.long}</span>
              </h4>
              {g === "llm" ? (
                llmParts.length ? (
                  <ul className="mt-1.5 space-y-1">
                    {llmParts.map((p, n) => (
                      <li key={n} data-testid="llm-text">
                        <span className="block text-xs font-medium text-warn">{p.label ?? `LLM-generated (${p.method.replace(/^ollama:/, "")}), may be imprecise`}</span>
                        {p.text}{" "}
                        <span className="text-xs text-subtle">
                          · <span className="mono">{p.method}</span> · rephrases the rule findings only; does not change the score, level or category
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-xs text-muted">No LLM text: {llmReason(a)}. The LLM never produces indicators or scores.</p>
                )
              ) : items.length === 0 ? (
                <p className="mt-1 text-xs text-muted">None from this source.</p>
              ) : (
                <ul className="mt-1.5 space-y-1.5">
                  {items.map((i, n) => (
                    <IndicatorItem key={`${i.code}-${n}`} i={i} />
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </section>

      {parts.length > 0 && (
        <section aria-labelledby={id("expl")}>
          {sectionHead(id("expl"), "Explanation")}
          <ul className="mt-1 space-y-1" data-testid="explanation-parts">
            {parts.filter((p) => p.source !== "llm").map((p, n) => (
              <li key={n} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <SourceBadge source={p.source} />
                <span>{p.text}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {a.recommended_actions.length > 0 && (
        <section aria-labelledby={id("act")}>
          {sectionHead(id("act"), <>Recommended actions <SourceBadge source="static_guidance" className="ml-1 align-middle normal-case tracking-normal" /></>)}
          <p className="text-xs text-subtle">General guidance, not a finding about this text.</p>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {a.recommended_actions.map((r, n) => (
              <li key={n}>{r.text}</li>
            ))}
          </ul>
        </section>
      )}

      {unavailable.length > 0 && (
        <section aria-labelledby={id("un")}>
          {sectionHead(id("un"), "Not analysed (capability unavailable)")}
          <ul className="mt-1 flex flex-wrap gap-1.5" data-testid="unavailable-capabilities">
            {unavailable.map(([k, s]) => (
              <li key={k} className="rounded-md border border-line bg-chip px-1.5 py-0.5 text-[11px] text-muted">
                {humanise(k)}: {s}
                {a.capability_reasons[k] ? ` (${humanise(a.capability_reasons[k]!).toLowerCase()})` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      {prov && (
        <details className="text-xs text-muted" data-testid="provenance">
          <summary className="cursor-pointer">
            Provenance{prov.derived_from_legacy ? " (derived: this analysis predates per-item provenance)" : ""}
          </summary>
          {prov.legacy ? (
            <p className="mt-1">{prov.note ?? "Not recorded for this analysis."}</p>
          ) : (
            <ul className="mt-1 space-y-0.5">
              {(prov.stages ?? []).map((s) => (
                <li key={s.stage}>
                  <SourceBadge source={s.source} /> {humanise(s.stage)} <span className="mono">{s.method}</span>: {humanise(s.status).toLowerCase()}
                  {s.reason ? ` (${humanise(s.reason).toLowerCase()})` : ""}
                </li>
              ))}
              {!prov.stages && prov.ran && <li>Ran: {prov.ran.map(humanise).join(", ")}</li>}
              {prov.note && <li>{prov.note}</li>}
            </ul>
          )}
        </details>
      )}

      <p className="text-xs text-subtle" data-testid="model-versions">
        {formatDateTime(a.created_at)} · pipeline <span className="mono">{a.pipeline_version}</span>
        {versions.length > 0 && (
          <>
            {" "}
            · models{" "}
            {versions.map(([k, v], n) => (
              <span key={k}>
                {n > 0 ? ", " : ""}
                {humanise(k).toLowerCase()} <span className="mono">{v}</span>
              </span>
            ))}
          </>
        )}
      </p>
      {footer}
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

function llmReason(a: AnalysisResult): string {
  const r = a.capability_reasons.llm_explanation;
  const st = a.capabilities.llm_explanation;
  if (!r || r === "not_configured") return "no local language model is configured";
  if (st === "rejected") return `the local model's text was rejected by the grounding checks (${humanise(r).toLowerCase()}); the rule explanation is used`;
  if (st === "timeout") return "the local model did not answer in time; the rule explanation is used";
  return humanise(r).toLowerCase();
}
