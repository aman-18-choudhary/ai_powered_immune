import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Cpu, Info, ListChecks, Mic, MicOff, Pause, Play, ScrollText, ShieldAlert, Square, Trash2, Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Panel, EmptyState } from "@/components/common/Panel";
import { cn } from "@/lib/utils/cn";
import { useMotionPrefs } from "@/lib/motion/prefs";
import { LIVE_LANGS, LIVE_LANG_NAMES, indicatorLabel, lt, type LiveKey, type LiveLang } from "@/lib/i18n/liveAudio";
import type { Indicator, Level, RiskPoint, SpeechLang, TranscriptSegment } from "@/lib/liveAudio/api";
import type { MicErrorKind } from "@/lib/liveAudio/capture";
import { teardownLiveAudio, useLiveAudio } from "@/state/liveAudio";

const UI_LANG_KEY = "ssi.live.lang";
const LEVEL_TONE: Record<Level, string> = {
  LOW: "text-safe border-[color-mix(in_srgb,var(--safe)_40%,transparent)] bg-safe-soft",
  MEDIUM: "text-warn border-[color-mix(in_srgb,var(--warn)_40%,transparent)] bg-warn-soft",
  HIGH: "text-danger-text border-[color-mix(in_srgb,var(--danger)_40%,transparent)] bg-danger-soft",
  CRITICAL: "text-danger-text border-danger bg-danger-soft",
};
const LEVEL_STROKE: Record<Level, string> = { LOW: "var(--safe)", MEDIUM: "var(--warn)", HIGH: "var(--danger)", CRITICAL: "var(--danger)" };
const MIC_ERR: Record<MicErrorKind, LiveKey> = {
  denied: "errDenied",
  no_device: "errNoDevice",
  in_use: "errInUse",
  insecure: "errInsecure",
  unsupported: "errUnsupported",
  unknown: "errUnknown",
};

function readLang(): LiveLang {
  try {
    const v = localStorage.getItem(UI_LANG_KEY);
    if (v === "en" || v === "hi") return v;
  } catch {
    /* storage unavailable */
  }
  return "en";
}

export function fmtClock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function fmtAt(s: number): string {
  return fmtClock(s * 1000);
}

function useElapsed(): number {
  const recordedMs = useLiveAudio((s) => s.recordedMs);
  const since = useLiveAudio((s) => s.runningSince);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (since == null) return;
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [since]);
  return recordedMs + (since != null ? Math.max(0, now - since) : 0);
}

function statusWord(lang: LiveLang, s: string | undefined | null): string {
  const map: Record<string, LiveKey> = {
    ready: "statusReady",
    configured: "statusReady",
    not_loaded: "statusNotLoaded",
    unavailable: "statusUnavailable",
    not_configured: "statusNotConfigured",
    reachable: "statusReachable",
    unreachable: "statusUnreachable",
  };
  const k = s ? map[s] : undefined;
  return k ? lt(lang, k) : (s ?? "…");
}

// ------------------------------------------------------------------------------- pieces
function LevelMeter({ level, label }: { level: number; label: string }) {
  const pct = Math.min(100, Math.round(Math.sqrt(level) * 140));
  return (
    <div className="flex items-center gap-2" aria-label={label}>
      <span className="text-[11px] text-subtle">{label}</span>
      <div className="h-2 w-28 overflow-hidden rounded-full bg-chip" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label={label}>
        <div className="h-full rounded-full bg-safe transition-[width] duration-100" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Gauge({ score, level, lang }: { score: number | null; level: Level | null; lang: LiveLang }) {
  const r = 46;
  const c = 2 * Math.PI * r;
  const frac = score == null ? 0 : score / 100;
  const stroke = level ? LEVEL_STROKE[level] : "var(--border-strong)";
  return (
    <div className="relative grid h-32 w-32 shrink-0 place-items-center">
      <svg viewBox="0 0 110 110" className="absolute inset-0 h-full w-full -rotate-90" aria-hidden>
        <circle cx="55" cy="55" r={r} fill="none" stroke="var(--border)" strokeWidth="10" />
        <circle cx="55" cy="55" r={r} fill="none" stroke={stroke} strokeWidth="10" strokeLinecap="round" strokeDasharray={`${c * frac} ${c}`} />
      </svg>
      <div className="text-center">
        <div className="mono tnum text-3xl font-semibold" data-testid="live-score">
          {score == null ? "–" : score}
        </div>
        <div className="text-[11px] text-subtle">/ 100</div>
        {level && <div className="sr-only">{lt(lang, `level${level}` as LiveKey)}</div>}
      </div>
    </div>
  );
}

function Sparkline({ points, label }: { points: RiskPoint[]; label: string }) {
  if (points.length < 2) return null;
  const w = 220;
  const h = 44;
  const step = w / Math.max(1, points.length - 1);
  const d = points.map((p, i) => `${(i * step).toFixed(1)},${(h - (p.score / 100) * h).toFixed(1)}`).join(" ");
  return (
    <figure className="mt-2" aria-label={`${label}: ${points.map((p) => p.score).join(", ")}`}>
      <svg viewBox={`0 0 ${w} ${h}`} className="h-11 w-full" preserveAspectRatio="none" aria-hidden data-testid="risk-sparkline">
        <line x1="0" x2={w} y1={h - 0.7 * h} y2={h - 0.7 * h} stroke="var(--danger)" strokeOpacity="0.35" strokeDasharray="3 3" />
        <line x1="0" x2={w} y1={h - 0.25 * h} y2={h - 0.25 * h} stroke="var(--warn)" strokeOpacity="0.35" strokeDasharray="3 3" />
        <polyline points={d} fill="none" stroke="var(--intel)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption className="text-[11px] text-subtle">{label}</figcaption>
    </figure>
  );
}

function TranscriptRow({ seg, lang, codes }: { seg: TranscriptSegment; lang: LiveLang; codes: Map<string, Indicator> }) {
  const flagged = seg.indicator_codes.length > 0;
  return (
    <li
      id={`seg-${seg.id}`}
      className={cn("rounded-lg border px-3 py-2", flagged ? "border-[color-mix(in_srgb,var(--danger)_45%,transparent)] bg-danger-soft" : "border-line bg-chip")}
      data-testid="transcript-segment"
    >
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-subtle">
        <span className="mono tnum">
          {fmtAt(seg.start_s)}–{fmtAt(seg.end_s)}
        </span>
        <span className="rounded border border-line px-1 uppercase">{seg.language}</span>
        <span className="mono">#{seg.id}</span>
      </div>
      <p className="mt-0.5 text-sm text-fg" lang={seg.language === "hi" ? "hi" : "en"}>
        {seg.text}
      </p>
      {flagged && (
        <ul className="mt-1 flex flex-wrap gap-1" aria-label={lt(lang, "indicatorsTitle")}>
          {seg.indicator_codes.map((c) => (
            <li key={c} className="rounded-full border border-line-strong px-2 py-0.5 text-[11px] text-danger-text">
              {indicatorLabel(lang, c, codes.get(c)?.label ?? c)}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

// ----------------------------------------------------------------------------- monitor
export function LiveMonitor() {
  const { reduced } = useMotionPrefs();
  const [lang, setLangState] = useState<LiveLang>(readLang);
  const [consent, setConsent] = useState(false);
  const st = useLiveAudio();
  const elapsed = useElapsed();
  const tr = (k: LiveKey, v?: Record<string, string | number>) => lt(lang, k, v);

  const setLang = (l: LiveLang) => {
    setLangState(l);
    try {
      localStorage.setItem(UI_LANG_KEY, l);
    } catch {
      /* ignore */
    }
  };

  const { loadCapabilities } = st;
  useEffect(() => {
    void loadCapabilities();
  }, [loadCapabilities]);
  // leaving the page (or switching to the simulation tab) always turns the microphone off
  useEffect(() => () => void teardownLiveAudio(), []);

  const active = st.phase === "recording" || st.phase === "paused";
  const busy = st.phase === "requesting" || st.phase === "starting" || st.phase === "stopping";
  const chunkS = Math.round((st.caps?.chunk_ms ?? st.chunkMs) / 1000);
  const codes = useMemo(() => new Map(st.indicators.map((i) => [i.code, i])), [st.indicators]);
  const sttDown = st.caps?.stt.status === "unavailable" || st.status?.stt === "unavailable";
  const latestWarning = st.warnings[st.warnings.length - 1];
  const level = st.risk?.level ?? null;

  const phaseText =
    st.phase === "requesting" ? tr("requesting") : st.phase === "starting" ? tr("starting") : st.phase === "stopping" ? tr("stopping") : st.phase === "recording" ? tr("micOn") : st.phase === "paused" ? tr("micPaused") : tr("micOff");

  return (
    <div className="space-y-3" lang={lang}>
      <div aria-live="polite" role="status" className="sr-only" data-testid="live-phase">
        {phaseText}
      </div>
      <div aria-live="assertive" role="alert" className="sr-only" data-testid="live-warning-region">
        {latestWarning ? `${tr(`level${latestWarning.level}` as LiveKey)}. ${latestWarning.message}` : ""}
      </div>

      {/* ---------------------------------------------------------------- controls */}
      <Panel as="section" aria-label={tr("title")} bodyClassName="space-y-3 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl font-semibold tracking-tight">{tr("title")}</h1>
            <p className="text-sm text-muted">{tr("subtitle")}</p>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <label className="flex items-center gap-1.5 text-muted">
              {tr("uiLanguage")}
              <select className="rounded-lg border border-line bg-chip px-2 py-1 text-fg" value={lang} onChange={(e) => setLang(e.target.value as LiveLang)} data-testid="live-ui-lang">
                {LIVE_LANGS.map((l) => (
                  <option key={l} value={l}>
                    {LIVE_LANG_NAMES[l]}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1.5 text-muted">
              {tr("speechLanguage")}
              <select
                className="rounded-lg border border-line bg-chip px-2 py-1 text-fg"
                value={st.language}
                disabled={active || busy}
                onChange={(e) => st.setLanguage(e.target.value as SpeechLang)}
                data-testid="live-speech-lang"
              >
                <option value="auto">{tr("speechAuto")}</option>
                <option value="hi">{tr("speechHi")}</option>
                <option value="en">{tr("speechEn")}</option>
              </select>
            </label>
          </div>
        </div>

        {!active && st.phase !== "stopping" && (
          <div className="rounded-xl border border-line bg-chip p-3" data-testid="consent-card">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <Info className="h-4 w-4 text-intel" aria-hidden /> {tr("consentTitle")}
            </h2>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted">
              <li>{tr("consent1", { s: chunkS })}</li>
              <li>{tr("consent2")}</li>
              <li>{tr("consent3")}</li>
              <li>{tr("consent4")}</li>
            </ul>
            <label className="mt-3 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="h-4 w-4 accent-[var(--intel)]" data-testid="consent-check" />
              {tr("consentCheck")}
            </label>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          {!active ? (
            <Button variant="primary" onClick={() => void st.start()} disabled={!consent || busy} data-testid="live-start">
              <Mic className="h-4 w-4" aria-hidden /> {tr("start")}
            </Button>
          ) : (
            <>
              {st.phase === "recording" ? (
                <Button onClick={st.pause} data-testid="live-pause">
                  <Pause className="h-4 w-4" aria-hidden /> {tr("pause")}
                </Button>
              ) : (
                <Button onClick={st.resume} data-testid="live-resume">
                  <Play className="h-4 w-4" aria-hidden /> {tr("resume")}
                </Button>
              )}
              <Button variant="danger" onClick={() => void st.stop()} data-testid="live-stop">
                <Square className="h-4 w-4" aria-hidden /> {tr("stop")}
              </Button>
            </>
          )}
          {st.phase === "ended" && (
            <Button variant="ghost" onClick={st.clear} data-testid="live-clear">
              <Trash2 className="h-4 w-4" aria-hidden /> {tr("clear")}
            </Button>
          )}
          <div className="ml-auto flex flex-wrap items-center gap-4 text-sm">
            <span className="flex items-center gap-2" data-testid="mic-indicator">
              {st.phase === "recording" ? (
                <span className={cn("h-2.5 w-2.5 rounded-full bg-danger", !reduced && "animate-pulse")} aria-hidden />
              ) : st.phase === "paused" ? (
                <Pause className="h-3.5 w-3.5 text-warn" aria-hidden />
              ) : (
                <MicOff className="h-3.5 w-3.5 text-subtle" aria-hidden />
              )}
              <span className={st.phase === "recording" ? "font-medium text-fg" : "text-muted"}>{phaseText}</span>
            </span>
            <span className="text-muted">
              {tr("elapsed")}{" "}
              <span className="mono tnum text-fg" data-testid="live-elapsed">
                {fmtClock(elapsed)}
              </span>
            </span>
            {active && <LevelMeter level={st.level} label={tr("inputLevel")} />}
          </div>
        </div>
        {(st.pending > 0 || st.lastLatencyMs != null) && (
          <p className="text-xs text-subtle">
            {st.pending > 0 && <span>{tr("queue", { n: st.pending })}. </span>}
            {st.lastLatencyMs != null && <span data-testid="live-latency">{tr("latency", { ms: st.lastLatencyMs })}</span>}
          </p>
        )}
        {st.micError && (
          <p role="alert" className="rounded-lg border border-danger bg-danger-soft px-3 py-2 text-sm text-danger-text" data-testid="mic-error">
            {tr(MIC_ERR[st.micError])}
          </p>
        )}
        {st.serverError && (
          <p role="alert" className="rounded-lg border border-danger bg-danger-soft px-3 py-2 text-sm text-danger-text" data-testid="server-error">
            {tr("errServer", { code: st.serverError })}
          </p>
        )}
        {sttDown && (
          <p role="alert" className="rounded-lg border border-warn bg-warn-soft px-3 py-2 text-sm text-fg" data-testid="stt-down">
            <AlertTriangle className="mr-1 inline h-4 w-4 text-warn" aria-hidden />
            {tr("sttDown")}
          </p>
        )}
        {st.phase === "ended" && <p className="text-xs text-muted">{tr("ended")}</p>}
      </Panel>

      {/* ---------------------------------------------------------------- results */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <Panel title={tr("transcriptTitle")} icon={<ScrollText className="h-3.5 w-3.5" aria-hidden />} className="xl:col-span-2" bodyClassName="p-3">
          <p className="mb-2 text-[11px] text-subtle">{tr("transcriptNote")}</p>
          {st.transcript.length === 0 ? (
            <EmptyState title={tr("transcriptEmpty")} />
          ) : (
            <ol className="max-h-[480px] space-y-1.5 overflow-y-auto pr-1" aria-label={tr("transcriptTitle")} data-testid="transcript">
              {st.transcript.map((s) => (
                <TranscriptRow key={s.id} seg={s} lang={lang} codes={codes} />
              ))}
            </ol>
          )}
        </Panel>

        <div className="space-y-3">
          <Panel title={tr("riskTitle")} icon={<ShieldAlert className="h-3.5 w-3.5" aria-hidden />} bodyClassName="p-3">
            <div className="flex items-center gap-4">
              <Gauge score={st.risk?.score ?? null} level={level} lang={lang} />
              <div className="min-w-0 space-y-1">
                {level ? (
                  <span className={cn("inline-block rounded-full border px-2.5 py-0.5 text-sm font-semibold", LEVEL_TONE[level])} data-testid="live-level">
                    {tr(`level${level}` as LiveKey)}
                  </span>
                ) : (
                  <span className="text-sm text-muted" data-testid="live-level">
                    {tr("riskNotAssessed")}
                  </span>
                )}
                {st.risk?.stale && (
                  <p className="text-xs text-warn" data-testid="risk-stale">
                    {tr("riskStale")}
                  </p>
                )}
                <p className="text-[11px] text-subtle">{tr("riskSemantics")}</p>
                <p className="text-[11px] font-medium text-muted">{tr("statement")}</p>
              </div>
            </div>
            <Sparkline points={st.risk?.history_tail ?? []} label={tr("historyLabel")} />
            {st.explanation && (st.risk?.assessed_chunks ?? 0) > 0 && (
              <div className="mt-2 rounded-lg border border-line bg-chip p-2 text-xs text-muted">
                <div className="font-semibold text-fg">{tr("explanationTitle")}</div>
                <p data-testid="rules-explanation">{st.explanation.rules}</p>
                {st.explanation.llm && (
                  <p className="mt-1" data-testid="llm-explanation">
                    <span className="block text-[11px] font-medium text-warn" data-testid="llm-label">
                      {st.explanation.llm_label ?? tr("llmModelLabel", { model: st.explanation.llm_model ?? st.caps?.llm.model ?? "local" })}
                    </span>
                    <span className="mr-1 rounded border border-line px-1 text-[10px] uppercase">{tr("llmTag")}</span>
                    {st.explanation.llm}
                  </p>
                )}
                {!st.explanation.llm && st.explanation.llm_status === "rejected" && (
                  <p className="mt-1 text-[11px] text-subtle" data-testid="llm-rejected">
                    {tr("llmRejected", { reason: (st.explanation.llm_reason ?? "rejected").replace(/_/g, " ") })}
                  </p>
                )}
              </div>
            )}
          </Panel>

          <Panel title={tr("indicatorsTitle")} icon={<AlertTriangle className="h-3.5 w-3.5" aria-hidden />} bodyClassName="p-3">
            {st.indicators.length === 0 ? (
              <EmptyState title={tr("indicatorsEmpty")} />
            ) : (
              <ul className="space-y-2" data-testid="indicators">
                {st.indicators.map((i) => (
                  <li key={i.code} className="rounded-lg border border-line bg-chip p-2" data-testid={`indicator-${i.code}`}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-sm font-medium">{indicatorLabel(lang, i.code, i.label)}</span>
                      <span className="rounded-full border border-warn px-1.5 py-0.5 text-[10px] uppercase text-warn">{tr("detectedNotConfirmed")}</span>
                    </div>
                    {i.evidence_excerpts.length > 0 && (
                      <p className="mt-1 text-xs text-muted">
                        {tr("heard")}: {i.evidence_excerpts.map((x) => `“${x}”`).join(", ")}
                      </p>
                    )}
                    <p className="mt-0.5 text-[11px] text-subtle">
                      {tr("triggeredBy")}:{" "}
                      {i.segment_ids.map((id, n) => (
                        <span key={id}>
                          {n > 0 && ", "}
                          <a href={`#seg-${id}`} className="mono underline decoration-dotted">
                            #{id}
                          </a>
                        </span>
                      ))}{" "}
                      · <span className="mono">{i.source}</span>
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Panel title={tr("warningsTitle")} icon={<ShieldAlert className="h-3.5 w-3.5" aria-hidden />} bodyClassName="p-3">
          {st.warnings.length === 0 ? (
            <EmptyState title={tr("warningsEmpty")} />
          ) : (
            <ol className="space-y-1.5" data-testid="warnings">
              {[...st.warnings].reverse().map((w) => (
                <li key={`${w.id}-${w.seq}`} className={cn("rounded-lg border px-2 py-1.5 text-xs", LEVEL_TONE[w.level])}>
                  <div className="flex justify-between gap-2 font-semibold">
                    <span>{tr(`level${w.level}` as LiveKey)}</span>
                    <span className="mono tnum">
                      {fmtAt(w.at_s)} · {new Date(w.wall).toLocaleTimeString()}
                    </span>
                  </div>
                  <p className="text-fg">{w.message}</p>
                </li>
              ))}
            </ol>
          )}
        </Panel>

        <Panel title={tr("actionsTitle")} icon={<ListChecks className="h-3.5 w-3.5" aria-hidden />} className="xl:col-span-2" bodyClassName="p-3">
          {st.actions.length === 0 ? (
            <EmptyState title={tr("actionsEmpty")} />
          ) : (
            <>
              <ol className="list-decimal space-y-1 pl-5 text-sm" data-testid="actions">
                {st.actions.map((a) => (
                  <li key={a.id}>{a.text}</li>
                ))}
              </ol>
              <p className="mt-2 text-[11px] text-subtle">{tr("actionsSource")}</p>
            </>
          )}
        </Panel>

        <div className="space-y-3">
          <Panel title={tr("servicesTitle")} icon={<Cpu className="h-3.5 w-3.5" aria-hidden />} bodyClassName="p-3">
            {st.capsError ? (
              <p className="text-xs text-danger-text">{tr("capsError")}</p>
            ) : (
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs" data-testid="services">
                <dt className="text-subtle">{tr("sttLabel")}</dt>
                <dd>
                  {st.caps ? `${st.caps.stt.engine} · ${st.caps.stt.model} · ${statusWord(lang, st.caps.stt.status)}` : "…"}
                  {st.caps?.stt.reason ? ` (${st.caps.stt.reason})` : ""}
                </dd>
                <dt className="text-subtle">{tr("scorerLabel")}</dt>
                <dd>{st.caps ? `${st.caps.scorer.model_version ?? "–"} · ${statusWord(lang, st.caps.scorer.status)}` : "…"}</dd>
                <dt className="text-subtle">{tr("llmLabel")}</dt>
                <dd>{st.caps ? `${statusWord(lang, st.caps.llm.status)}${st.caps.llm.model ? ` · ${st.caps.llm.model}` : ""}` : "…"}</dd>
                <dt className="text-subtle">{tr("modeLabel")}</dt>
                <dd>{tr("modeValue", { s: chunkS })}</dd>
              </dl>
            )}
          </Panel>
          <Panel title={tr("errorsTitle")} icon={<Wrench className="h-3.5 w-3.5" aria-hidden />} bodyClassName="p-3">
            {st.errors.length === 0 ? (
              <p className="text-xs text-subtle">{tr("errorsEmpty")}</p>
            ) : (
              <ul className="space-y-1 text-xs" data-testid="errors">
                {st.errors.slice(-8).map((e) => (
                  <li key={e.id} className="flex justify-between gap-2">
                    <span>
                      <span className="mono">{e.stage}</span>: {e.code}
                      {e.seq != null ? ` (chunk ${e.seq})` : ""}
                    </span>
                    <span className="mono text-subtle">{new Date(e.at).toLocaleTimeString()}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>

      <p className="flex items-start gap-2 rounded-xl border border-line bg-chip px-3 py-2 text-xs text-muted" data-testid="live-honest">
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-intel" aria-hidden />
        <span>{tr("noFake")}</span>
      </p>
    </div>
  );
}
