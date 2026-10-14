import { lazy, Suspense, useEffect, useRef, useState, type FormEvent } from "react";
import { BadgeCheck, KeyRound, Mail, RefreshCw, Terminal, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Field, inputCls, Spinner } from "@/components/complaints/ui";
import { complaintsApi, messageFor } from "@/lib/complaints/api";
import { useAccount, type Entry } from "@/state/account";
import { cn } from "@/lib/utils/cn";

const ClerkSignInPanel = lazy(() => import("./clerk").then((m) => ({ default: m.ClerkSignInPanel })));

const ENTRIES: { entry: Entry; title: string; blurb: string; Icon: typeof UserRound }[] = [
  { entry: "citizen", title: "Citizen", blurb: "Report a cyber fraud, add evidence and follow your complaint.", Icon: UserRound },
  {
    entry: "investigator",
    title: "Investigator",
    blurb: "Approved accounts only. The server checks your verified email; if it is not approved you continue as a citizen.",
    Icon: BadgeCheck,
  },
];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function EntryChoice() {
  const entry = useAccount((s) => s.pendingEntry);
  const setEntry = useAccount((s) => s.setPendingEntry);
  return (
    <div role="radiogroup" aria-label="Sign in as" className="grid gap-2 sm:grid-cols-2">
      {ENTRIES.map(({ entry: e, title, blurb, Icon }) => (
        <button
          key={e}
          type="button"
          role="radio"
          aria-checked={entry === e}
          onClick={() => setEntry(e)}
          data-testid={`entry-${e}`}
          className={cn(
            "flex items-start gap-3 rounded-xl border p-3 text-left transition-colors",
            entry === e ? "border-intel bg-intel-soft" : "border-line bg-chip hover:bg-hover",
          )}
        >
          <Icon className={cn("mt-0.5 h-5 w-5 shrink-0", entry === e ? "text-intel" : "text-muted")} aria-hidden />
          <span>
            <span className="block text-sm font-semibold">{title}</span>
            <span className="mt-0.5 block text-xs text-muted">{blurb}</span>
          </span>
        </button>
      ))}
    </div>
  );
}

function LocalSignIn({ ttl }: { ttl: number | null }) {
  const entry = useAccount((s) => s.pendingEntry);
  const complete = useAccount((s) => s.completeLocalSignIn);
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErr, setFieldErr] = useState<{ email?: string; code?: string }>({});
  const [status, setStatus] = useState("");
  const codeRef = useRef<HTMLInputElement>(null);

  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    setError(null);
    if (!EMAIL_RE.test(email.trim())) {
      setFieldErr({ email: "Enter a valid email address." });
      return;
    }
    setFieldErr({});
    setBusy(true);
    try {
      const r = await complaintsApi.localStart(email.trim());
      setStep("code");
      setCode("");
      setStatus(r.message);
      setTimeout(() => codeRef.current?.focus(), 0);
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setBusy(false);
    }
  };

  const verify = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!/^\d{6}$/.test(code)) {
      setFieldErr({ code: "Enter the 6-digit code." });
      return;
    }
    setFieldErr({});
    setBusy(true);
    try {
      const r = await complaintsApi.localVerify(email.trim(), code, name.trim() || undefined);
      setStatus("Signed in. Checking your account…");
      await complete(r.token, entry);
    } catch (err) {
      setError(messageFor(err));
      setStatus("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3" data-testid="local-sign-in">
      <p className="flex items-start gap-2 rounded-xl border border-warn/40 bg-warn-soft p-3 text-sm" data-testid="local-dev-note">
        <Terminal className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
        <span>
          <strong>Local development sign-in: the code is printed in the server terminal, not emailed.</strong> Anyone who can read
          that terminal can sign in, so use it only on your own machine.
        </span>
      </p>
      {step === "email" ? (
        <form onSubmit={send} className="space-y-3" noValidate>
          <Field label="Email" error={fieldErr.email}>
            {(p) => (
              <input {...p} type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputCls} data-testid="local-email" />
            )}
          </Field>
          <Field label="Display name" optional hint="Shown on your profile. You can change it later.">
            {(p) => <input {...p} autoComplete="name" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} className={inputCls} />}
          </Field>
          <Button type="submit" variant="primary" className="w-full" disabled={busy} data-testid="local-send">
            {busy ? <Spinner /> : <Mail className="h-4 w-4" aria-hidden />} Send sign-in code
          </Button>
        </form>
      ) : (
        <form onSubmit={verify} className="space-y-3" noValidate>
          <p className="text-sm text-muted">
            Code requested for <strong className="text-fg">{email.trim()}</strong>
            {ttl ? `. It expires in ${Math.round(ttl / 60)} minutes.` : "."}
          </p>
          <Field label="6-digit code" error={fieldErr.code}>
            {(p) => (
              <input
                {...p}
                ref={codeRef}
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]*"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                className={cn(inputCls, "mono tracking-[0.4em]")}
                data-testid="local-code"
              />
            )}
          </Field>
          <Button type="submit" variant="primary" className="w-full" disabled={busy} data-testid="local-verify">
            {busy ? <Spinner /> : <KeyRound className="h-4 w-4" aria-hidden />} Verify and sign in
          </Button>
          <div className="flex flex-wrap justify-between gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setStep("email")} disabled={busy}>
              Use a different email
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => void send()} disabled={busy}>
              <RefreshCw className="h-3.5 w-3.5" aria-hidden /> Send a new code
            </Button>
          </div>
        </form>
      )}
      <p role="status" aria-live="polite" className="text-xs text-subtle" data-testid="local-status">
        {status}
      </p>
      {error && (
        <p role="alert" className="rounded-xl border border-danger/40 bg-danger-soft p-3 text-sm" data-testid="sign-in-error">
          {error}
        </p>
      )}
    </div>
  );
}

/** Account sign-in for the real complaints service. Methods come from GET /auth/config. */
export function AccountSignIn() {
  const config = useAccount((s) => s.config);
  const configStatus = useAccount((s) => s.configStatus);
  const configError = useAccount((s) => s.configError);
  const loadConfig = useAccount((s) => s.loadConfig);
  const notice = useAccount((s) => s.notice);
  const status = useAccount((s) => s.status);
  const error = useAccount((s) => s.error);

  useEffect(() => {
    if (configStatus === "idle") void loadConfig();
  }, [configStatus, loadConfig]);

  let method: React.ReactNode;
  if (configStatus === "idle" || configStatus === "loading") {
    method = (
      <p role="status" className="flex items-center gap-2 text-sm text-muted">
        <Spinner /> Checking which sign-in methods are available…
      </p>
    );
  } else if (configStatus === "unavailable" || !config) {
    method = (
      <div role="alert" className="space-y-2 rounded-xl border border-danger/40 bg-danger-soft p-3 text-sm" data-testid="auth-unavailable">
        <p className="font-medium">Account sign-in is unavailable right now.</p>
        <p className="text-muted">{configError ?? "The complaints service did not answer."}</p>
        <Button size="sm" variant="secondary" onClick={() => void loadConfig()}>
          <RefreshCw className="h-3.5 w-3.5" aria-hidden /> Try again
        </Button>
      </div>
    );
  } else if (config.clerk.enabled) {
    method = config.clerk.publishable_key ? (
      <Suspense
        fallback={
          <p role="status" className="flex items-center gap-2 text-sm text-muted">
            <Spinner /> Loading secure sign-in…
          </p>
        }
      >
        <ClerkSignInPanel />
      </Suspense>
    ) : (
      <p role="alert" className="rounded-xl border border-warn/40 bg-warn-soft p-3 text-sm" data-testid="clerk-missing-key">
        Sign-in uses Clerk on this server, but no publishable key is configured (CLERK_PUBLISHABLE_KEY), so the sign-in form cannot be shown.
      </p>
    );
  } else if (config.local.enabled) {
    method = <LocalSignIn ttl={config.local.code_ttl_s} />;
  } else {
    method = (
      <p role="alert" className="text-sm text-muted">
        No sign-in method is enabled on this server.
      </p>
    );
  }

  return (
    <section aria-labelledby="acct-h" className="panel panel-blur space-y-4 p-5" data-testid="account-sign-in">
      <div>
        <h2 id="acct-h" className="text-lg font-semibold">
          Sign in to report and track complaints
        </h2>
        <p className="mt-1 text-sm text-muted">Real records: complaints you register here are stored by the complaints service.</p>
      </div>
      {notice === "expired" && (
        <p role="status" className="rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-sm text-warn">
          Your sign-in expired. Please sign in again.
        </p>
      )}
      {status === "error" && error && (
        <p role="alert" className="rounded-lg border border-danger/40 bg-danger-soft px-3 py-2 text-sm">
          {error}
        </p>
      )}
      <EntryChoice />
      {method}
    </section>
  );
}
