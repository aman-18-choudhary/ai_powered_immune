import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Eye, FlaskConical, Loader2, Play, Radio } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/common/Panel";
import { PhonePane } from "@/components/player/PhonePane";
import { BankPane } from "@/components/player/BankPane";
import { MapPane } from "@/components/player/MapPane";
import { Timeline, type Marker } from "@/components/player/Timeline";
import { Controls } from "@/components/player/Controls";
import { FinaleCard } from "@/components/player/FinaleCard";
import { HONEST_LABEL, STEP_ORDER } from "@/components/player/notes";
import { useRunStore } from "@/state/runStore";
import { useSession, canControl, canResolve } from "@/state/session";
import { useSimTime } from "@/state/useSimTime";
import { DEFAULT_UI_SPEED, playback, serverToUiSpeed } from "@/state/playback";
import { endpoints } from "@/lib/api/endpoints";
import { friendlyError } from "@/lib/api/client";
import { toast } from "@/state/toasts";
import { loadStepTimes, saveStepTimes } from "@/lib/stepMemory";
import { useEvidence } from "@/state/evidence";
import { useMotionPrefs } from "@/lib/motion/prefs";
import type { HoldRecord } from "@/state/reducer";

function Hero({ onPlay, disabled, starting, note }: { onPlay: () => void; disabled: boolean; starting: boolean; note?: string }) {
  const { reduced } = useMotionPrefs();
  return (
    <div className="relative mx-auto flex min-h-[calc(100dvh-120px)] max-w-5xl flex-col items-center justify-center px-4 py-12 text-center">
      <motion.div initial={reduced ? false : { opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}>
        <div className="mx-auto mb-6 inline-flex items-center gap-2 rounded-full border border-line bg-chip px-3 py-1 text-xs text-muted">
          <FlaskConical className="h-3.5 w-3.5 text-warn" aria-hidden /> A simulated digital-arrest scam, played against the real detection code
        </div>
        <h1 className="mx-auto max-w-3xl text-4xl font-semibold leading-[1.1] tracking-tight sm:text-6xl">
          One bank&apos;s detection{" "}
          <span className="bg-[linear-gradient(135deg,var(--intel),var(--flow))] bg-clip-text text-transparent">protects every bank.</span>
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-lg text-muted">
          Watch a scam call get flagged, a transfer held, a mule confirmed, and the <em className="not-italic text-fg">second</em> bank block
          the same mule seconds later, with a court-verifiable evidence package at the end.
        </p>
        <div className="mt-8 flex flex-col items-center gap-3">
          <Button size="lg" variant="primary" onClick={onPlay} disabled={disabled || starting} data-testid="hero-play" className="px-8">
            {starting ? <Loader2 className="h-5 w-5 animate-spin" aria-hidden /> : <Play className="h-5 w-5" aria-hidden />}
            Play the interception
          </Button>
          {note && <p className="max-w-md text-sm text-muted">{note}</p>}
        </div>
      </motion.div>

      <ol className="mt-14 grid w-full max-w-4xl gap-3 text-left sm:grid-cols-4">
        {[
          ["1", "Victim A's phone", "Call-guard scores the call as it unfolds."],
          ["2", "Bank A", "Transfer held; an analyst confirms the mule."],
          ["3", "Bank B", "Same mule, different bank, blocked by the shared antibody."],
          ["4", "Command centre", "Payment paths, the forming ring, and the evidence ledger."],
        ].map(([n, t, d]) => (
          <li key={n} className="panel panel-blur p-4">
            <span className="mono grid h-6 w-6 place-items-center rounded-md bg-intel-soft text-xs font-semibold text-intel">{n}</span>
            <div className="mt-2 text-sm font-semibold">{t}</div>
            <div className="mt-0.5 text-xs text-muted">{d}</div>
          </li>
        ))}
      </ol>
      <p className="mt-8 max-w-2xl text-xs text-subtle">{HONEST_LABEL}</p>
    </div>
  );
}

export default function Landing() {
  const run = useRunStore((s) => s.run);
  const conn = useRunStore((s) => s.conn);
  const role = useSession((s) => s.role);
  const simT = useSimTime();
  const { reduced } = useMotionPrefs();
  const [uiSpeed, setUiSpeed] = useState<number | null>(null);
  const [starting, setStarting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [finaleDismissed, setFinaleDismissed] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const [finaleMsg, setFinaleMsg] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const control = canControl(role);

  const hasRun = run.hasData && run.status !== "idle";
  const running = run.status === "running" || run.status === "starting" || run.status === "seeking";
  const finished = run.status === "finished";
  const speedShown = hasRun ? serverToUiSpeed(run.speed) : (uiSpeed ?? DEFAULT_UI_SPEED);

  // ---- step memory so future markers exist on a replay ------------------------------------
  const [remembered, setRemembered] = useState<Record<string, number>>({});
  useEffect(() => {
    setRemembered(loadStepTimes(run.seed, run.mode));
  }, [run.seed, run.mode, run.runId]);
  useEffect(() => {
    if (run.steps.length === 0) return;
    const times: Record<string, number> = { ...loadStepTimes(run.seed, run.mode) };
    for (const s of run.steps) times[s.key] = s.t;
    saveStepTimes(run.seed, run.mode, times);
  }, [run.steps, run.seed, run.mode]);

  const reached = useMemo(() => new Set(run.steps.map((s) => s.key)), [run.steps]);
  const markers: Marker[] = useMemo(() => {
    const out = new Map<string, Marker>();
    const titles = new Map(run.steps.map((s) => [s.key, s.title]));
    for (const [key, t] of Object.entries(remembered)) out.set(key, { key, t, title: titles.get(key) ?? key });
    for (const s of run.steps) out.set(s.key, { key: s.key, t: s.t, title: s.title });
    return [...out.values()].sort((a, b) => a.t - b.t);
  }, [run.steps, remembered]);
  const currentStep = run.steps[run.steps.length - 1];

  // ---- announcements ----------------------------------------------------------------------
  useEffect(() => {
    if (currentStep) setAnnounce(`${currentStep.title}. ${currentStep.caption}`);
  }, [currentStep]);
  useEffect(() => {
    if (finished && run.finished && run.runId) {
      setFinaleMsg(
        run.finished.victimBProtected
          ? `Victim B protected${run.finished.latencyMs != null ? ` in ${run.finished.latencyMs.toFixed(1)} milliseconds` : ""}.`
          : "Run complete. No antibody was shared.",
      );
    } else {
      setFinaleMsg("");
    }
  }, [finished, run.finished, run.runId]);

  // ---- actions ----------------------------------------------------------------------------
  const start = useCallback(async () => {
    setStarting(true);
    useEvidence.getState().reset();
    setFinaleDismissed(null);
    try {
      await playback.start(uiSpeed ?? undefined);
    } finally {
      setStarting(false);
    }
  }, [uiSpeed]);

  const toggle = useCallback(() => {
    if (!control) return;
    void (running ? playback.pause() : playback.resume());
  }, [control, running]);

  const onSpeed = useCallback(
    (s: number) => {
      setUiSpeed(s);
      if (hasRun && control) void playback.setSpeed(s);
    },
    [hasRun, control],
  );

  const seek = useCallback((t: number) => void playback.seek(t), []);

  const stepBy = useCallback(
    (dir: 1 | -1) => {
      if (!control || !hasRun) return;
      const now = useRunStore.getState().clock.t;
      const ts = markers.map((m) => m.t);
      if (dir === 1) {
        const next = ts.find((t) => t > now + 0.5);
        seek(next ?? Math.min(run.duration, now + 120));
      } else {
        const prev = [...ts].reverse().find((t) => t < now - 0.5);
        seek(prev ?? 0);
      }
    },
    [control, hasRun, markers, run.duration, seek],
  );

  const fullscreen = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void el.requestFullscreen?.().catch(() => undefined);
  }, []);

  const confirm = useCallback(async (h: HoldRecord) => {
    setConfirming(true);
    try {
      await endpoints.resolveHold(h.txn_ref, "confirm_block", h.decision_seq);
      toast.success("Mule confirmed", "The antibody is being published.");
    } catch (e) {
      toast.error("Could not confirm", friendlyError(e));
    } finally {
      setConfirming(false);
    }
  }, []);

  // ---- keyboard ---------------------------------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON" || el?.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === " " || e.code === "Space") {
        e.preventDefault();
        if (!hasRun) void start();
        else if (run.status === "finished" || run.status === "stopped") void start();
        else toggle();
      } else if (e.key === "ArrowRight") {
        stepBy(1);
      } else if (e.key === "ArrowLeft") {
        stepBy(-1);
      } else if (e.key === "f" || e.key === "F") {
        fullscreen();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hasRun, run.status, start, toggle, stepBy, fullscreen]);

  const sr = (
    <>
      <div aria-live="polite" role="status" className="sr-only" data-testid="live-region">
        {announce}
      </div>
      <div aria-live="assertive" role="alert" className="sr-only" data-testid="finale-region">
        {finaleMsg}
      </div>
    </>
  );

  if (!hasRun) {
    return (
      <>
        {sr}
        <Hero
          onPlay={() => void start()}
          disabled={!control || conn === "idle"}
          starting={starting}
          note={
            !control
              ? "Sign in as Officer or Admin (top right, Switch role) to start a run. Analysts and citizens can watch a run started by someone else."
              : conn !== "open"
                ? "Connecting to the live stream…"
                : undefined
          }
        />
      </>
    );
  }

  const stepIdx = currentStep ? Math.max(0, STEP_ORDER.indexOf(currentStep.key === "bank_b_misses" ? "bank_b_blocks" : (currentStep.key as (typeof STEP_ORDER)[number]))) : -1;
  const showFinale = finished && run.finished && finaleDismissed !== run.runId;

  return (
    <div ref={containerRef} className="mx-auto max-w-[1680px] space-y-3 bg-bg px-4 py-4 md:px-6">
      {sr}
      <Panel as="section" className="p-0" bodyClassName="space-y-3 p-4" aria-label="Playback">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-subtle">
              <Radio className="h-3.5 w-3.5 text-flow" aria-hidden />
              {currentStep ? `Step ${stepIdx + 1} of ${STEP_ORDER.length}` : "Starting"}
              <span className="rounded-full border border-line px-1.5 py-0.5 normal-case tracking-normal">
                {run.status === "paused" ? "Paused" : finished ? "Finished" : run.status === "stopped" ? "Stopped" : "Live"}
              </span>
            </div>
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={currentStep?.key ?? "none"}
                initial={reduced ? false : { opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
              >
                <h1 className="mt-1 text-xl font-semibold tracking-tight">{currentStep?.title ?? "Waiting for the first event…"}</h1>
                <p className="text-sm text-muted">{currentStep?.caption ?? "The scenario is warming up."}</p>
              </motion.div>
            </AnimatePresence>
          </div>
          {finished && run.finished && finaleDismissed === run.runId && (
            <Button variant="secondary" size="sm" onClick={() => setFinaleDismissed(null)}>
              <Eye className="h-4 w-4" aria-hidden /> Show result
            </Button>
          )}
        </div>
        <Controls
          status={run.status}
          speed={speedShown}
          disabled={!control}
          stepKey={currentStep?.key ?? null}
          onToggle={toggle}
          onRestart={() => void start()}
          onSpeed={onSpeed}
          onFullscreen={fullscreen}
        />
        {!control && (
          <p className="text-xs text-muted">Read-only: sign in as Officer or Admin to control playback.</p>
        )}
        <Timeline
          t={simT}
          duration={run.duration}
          markers={markers}
          reached={reached}
          currentKey={currentStep?.key ?? null}
          disabled={!control}
          onSeek={seek}
        />
      </Panel>

      <div className="relative">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4 xl:[grid-auto-rows:clamp(500px,calc(100dvh-410px),680px)]">
          <div className="h-[520px] xl:h-auto">
            <PhonePane run={run} />
          </div>
          <div className="h-[520px] xl:h-auto">
            <BankPane run={run} bank="bank_a" simT={simT} canResolve={canResolve(role)} onConfirm={confirm} confirming={confirming} />
          </div>
          <div className="h-[520px] xl:h-auto">
            <BankPane run={run} bank="bank_b" simT={simT} canResolve={false} />
          </div>
          <div className="h-[520px] xl:h-auto">
            <MapPane run={run} />
          </div>
        </div>
        <AnimatePresence>
          {showFinale && run.finished && (
            <motion.div
              key="finale"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 z-20 grid place-items-start justify-items-center rounded-2xl bg-[color-mix(in_srgb,var(--bg)_60%,transparent)] p-3 pt-10 backdrop-blur-[3px]"
            >
              <FinaleCard
                runId={run.runId}
                finished={run.finished}
                canExport={control}
                onClose={() => setFinaleDismissed(run.runId)}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <p
        className="flex items-start gap-2 rounded-xl border border-warn/30 bg-warn-soft px-3 py-2 text-xs text-muted"
        data-testid="honest-strip"
      >
        <FlaskConical className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" aria-hidden />
        <span>
          <strong className="text-fg">Honest label. </strong>
          {run.scenarioNote ?? HONEST_LABEL} <span className="text-subtle">Everything shown is simulated; timings are measured.</span>
        </span>
      </p>
    </div>
  );
}
