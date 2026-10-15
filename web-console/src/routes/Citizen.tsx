import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Lock, ShieldCheck } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { VerdictCard } from "@/components/citizen/VerdictCard";
import { endpoints } from "@/lib/api/endpoints";
import { ApiError } from "@/lib/api/client";
import { usePageTitle } from "@/lib/hooks/usePageTitle";
import { LANGS, LANG_NAMES, isLang, t, type CitizenKey, type Lang } from "@/lib/i18n/citizen";
import { INPUT_CAP, MAX_CHARS, SAMPLES, parseVerdict, prepareMessage, type Verdict } from "@/lib/citizen/verdict";
import { cn } from "@/lib/utils/cn";

const LANG_KEY = "ssi.lang";
function initialLang(): Lang {
  try {
    const s = sessionStorage.getItem(LANG_KEY);
    if (isLang(s)) return s;
  } catch {
    /* ignore */
  }
  const nav = typeof navigator !== "undefined" ? navigator.language.slice(0, 2) : "en";
  return isLang(nav) ? nav : "en";
}

type Phase =
  | { k: "input" }
  | { k: "analysing" }
  | { k: "verdict"; verdict: Verdict; truncated: boolean }
  | { k: "error"; key: CitizenKey };

export default function Citizen() {
  usePageTitle("Citizen Shield");
  const [lang, setLang] = useState<Lang>(initialLang);
  // The message lives only in this component's memory: never in storage, URL, logs or the store.
  const [text, setText] = useState("");
  const [touched, setTouched] = useState(false);
  const [phase, setPhase] = useState<Phase>({ k: "input" });
  const [reported, setReported] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);

  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    try {
      sessionStorage.setItem(LANG_KEY, lang);
    } catch {
      /* ignore */
    }
  }, [lang]);

  const len = text.trim().length;
  const over = len > MAX_CHARS;

  const submit = useCallback(async () => {
    setTouched(true);
    const p = prepareMessage(text);
    if (p.length === 0) {
      area.current?.focus();
      return;
    }
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setReported(false);
    setPhase({ k: "analysing" });
    const started = Date.now();
    try {
      const raw = await endpoints.assess(p.text, lang, ctl.signal);
      const v = parseVerdict(raw);
      // keep the analysing state visible for a moment so the transition reads as a check, not a flash
      const wait = Math.max(0, 450 - (Date.now() - started));
      if (wait) await new Promise((r) => setTimeout(r, wait));
      if (ctl.signal.aborted) return;
      setPhase(v ? { k: "verdict", verdict: v, truncated: p.truncated } : { k: "error", key: "errorGeneric" });
    } catch (e) {
      if (ctl.signal.aborted) return;
      const key: CitizenKey =
        e instanceof ApiError ? (e.code === "rate_limited" ? "errorBusy" : e.code === "network" ? "errorNetwork" : "errorGeneric") : "errorGeneric";
      setPhase({ k: "error", key });
    }
  }, [text, lang]);

  const reset = () => {
    abort.current?.abort();
    setText("");
    setTouched(false);
    setReported(false);
    setPhase({ k: "input" });
    setTimeout(() => area.current?.focus(), 0);
  };

  const showForm = phase.k === "input" || phase.k === "error";

  return (
    <div className="mx-auto flex w-full justify-center px-0 py-0 md:px-6 md:py-6" lang={lang}>
      <div
        className={cn(
          "relative w-full max-w-[430px] bg-bg",
          "md:rounded-[40px] md:border-[10px] md:border-[var(--phone-bezel)] md:shadow-[0_30px_80px_-30px_rgba(0,0,0,0.6)]",
        )}
        data-testid="phone-frame"
      >
        <div className="hidden justify-center pt-2 md:flex" aria-hidden>
          <span className="h-5 w-24 rounded-full bg-[var(--phone-bezel)]" />
        </div>
        <div className="space-y-4 px-4 pb-8 pt-4 md:min-h-[780px]">
          <header className="space-y-1">
            <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
              <ShieldCheck className="h-6 w-6 text-safe" aria-hidden /> {t(lang, "title")}
            </h1>
            <p className="text-[15px] leading-snug text-muted">{t(lang, "subtitle")}</p>
            <p className="text-xs text-subtle" data-testid="citizen-sim-note">
              Simulation demo. Signed in to an account? Use{" "}
              <Link to="/check" className="text-flow underline underline-offset-2">
                Check a message or link
              </Link>{" "}
              for the real checker.
            </p>
          </header>

          <div role="radiogroup" aria-label={t(lang, "language")} className="flex gap-1 rounded-2xl border border-line bg-chip p-1">
            {LANGS.map((l) => (
              <button
                key={l}
                type="button"
                role="radio"
                aria-checked={lang === l}
                lang={l}
                onClick={() => setLang(l)}
                className={cn(
                  "min-h-11 flex-1 rounded-xl px-2 text-sm font-medium transition-colors",
                  lang === l ? "bg-raised text-fg shadow" : "text-muted hover:text-fg",
                )}
              >
                {LANG_NAMES[l]}
              </button>
            ))}
          </div>

          <AnimatePresence mode="wait" initial={false}>
            {showForm && (
              <motion.form
                key="form"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.2 }}
                onSubmit={(e) => {
                  e.preventDefault();
                  void submit();
                }}
                className="space-y-3"
                noValidate
              >
                <div className="space-y-1.5">
                  <label htmlFor="msg" className="text-sm font-semibold">
                    {t(lang, "messageLabel")}
                  </label>
                  <textarea
                    id="msg"
                    ref={area}
                    value={text}
                    onChange={(e) => setText(e.target.value.slice(0, INPUT_CAP))}
                    rows={7}
                    placeholder={t(lang, "placeholder")}
                    aria-describedby="msg-hint msg-count"
                    aria-invalid={touched && len === 0}
                    autoComplete="off"
                    autoCorrect="off"
                    spellCheck={false}
                    className="w-full resize-y rounded-2xl border border-line-strong bg-sunken p-3.5 text-[16px] leading-snug outline-none focus-visible:border-intel"
                    data-testid="message"
                  />
                  <p id="msg-hint" className="text-xs text-subtle">
                    {t(lang, "pasteHint")}
                  </p>
                  <p id="msg-count" className={cn("mono tnum text-xs", over ? "font-semibold text-warn" : "text-subtle")} aria-live="polite" data-testid="counter">
                    {t(lang, "counter", { n: len, max: MAX_CHARS })}
                    {over && ` · ${t(lang, "overLimit", { max: MAX_CHARS })}`}
                  </p>
                  {touched && len === 0 && (
                    <p role="alert" className="text-sm text-danger-text">
                      {t(lang, "empty")}
                    </p>
                  )}
                </div>

                <div>
                  <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">{t(lang, "samples")}</p>
                  <div className="flex flex-wrap gap-2">
                    {SAMPLES.map((s) => (
                      <button
                        key={s.key}
                        type="button"
                        onClick={() => {
                          setText(s.text);
                          setTouched(false);
                          area.current?.focus();
                        }}
                        className="min-h-11 rounded-full border border-line-strong bg-chip px-3.5 text-sm font-medium hover:bg-hover"
                        data-testid={`sample-${s.key}`}
                      >
                        {t(lang, s.key)}
                      </button>
                    ))}
                  </div>
                  <p className="mt-1.5 text-xs text-subtle">{t(lang, "sampleNote")}</p>
                </div>

                {phase.k === "error" && (
                  <p role="alert" className="rounded-xl border border-danger/40 bg-danger-soft p-3 text-sm" data-testid="error">
                    {t(lang, phase.key)}
                  </p>
                )}
                <Button type="submit" variant="primary" size="lg" className="w-full" data-testid="check">
                  {phase.k === "error" ? t(lang, "retry") : t(lang, "check")}
                </Button>
              </motion.form>
            )}

            {phase.k === "analysing" && (
              <motion.div
                key="analysing"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="flex flex-col items-center gap-4 rounded-3xl border border-line bg-chip px-6 py-12 text-center"
                role="status"
                aria-live="polite"
                data-testid="analysing"
              >
                <span className="relative grid h-20 w-20 place-items-center">
                  <span className="absolute inset-0 animate-ping rounded-full bg-intel-soft" aria-hidden />
                  <Loader2 className="h-9 w-9 animate-spin text-intel" aria-hidden />
                </span>
                <p className="text-lg font-semibold">{t(lang, "analysing")}</p>
                <p className="max-w-[28ch] text-sm text-muted">{t(lang, "analysingSub")}</p>
              </motion.div>
            )}

            {phase.k === "verdict" && (
              <VerdictCard
                key="verdict"
                lang={lang}
                verdict={phase.verdict}
                truncated={phase.truncated}
                reported={reported}
                onReport={() => setReported(true)}
                onAgain={reset}
              />
            )}
          </AnimatePresence>

          <footer className="space-y-1.5 border-t border-line pt-3 text-xs text-subtle">
            <p className="flex items-start gap-1.5">
              <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /> <span data-testid="privacy">{t(lang, "privacy")}</span>
            </p>
            <p>{t(lang, "demoNote")}</p>
          </footer>
        </div>
      </div>
    </div>
  );
}
