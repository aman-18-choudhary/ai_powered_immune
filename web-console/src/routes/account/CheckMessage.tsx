import { useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, FilePlus2, Info, ScanSearch } from "lucide-react";
import { Panel } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { ErrorBlock, Field, PageHeader, Spinner, inputCls } from "@/components/complaints/ui";
import { AnalysisResultView } from "@/components/complaints/AnalysisResultView";
import { ComplaintsApiError, complaintsApi, messageFor } from "@/lib/complaints/api";
import { PAYMENT_LABEL, formatDateTime, humanise } from "@/lib/complaints/format";
import { PAYMENT_METHODS, type CheckResult, type PaymentMethod } from "@/lib/complaints/types";
import { EMPTY_CHECK, MAX_TEXT, MIN_TEXT, buildCheck, validateCheck, type CheckValues } from "@/lib/complaints/check";
import { usePageTitle } from "@/lib/hooks/usePageTitle";
import { useAccountRole } from "@/state/account";

const CAP_LABEL: Record<string, string> = {
  text: "Text",
  url: "Link",
  upi_id: "UPI ID",
  phone: "Phone",
  email: "Email",
  amount: "Amount",
  payment_method: "Payment method",
  audio: "Audio",
  screenshot: "Screenshot",
};

function InputCapabilities({ r }: { r: CheckResult }) {
  const ic = r.input_capabilities;
  if (!ic) return null;
  return (
    <section aria-labelledby="chk-inputs">
      <h3 id="chk-inputs" className="text-xs font-semibold uppercase tracking-wide text-subtle">
        What was done with each input
      </h3>
      <ul className="mt-1 grid gap-1 text-xs sm:grid-cols-2" data-testid="input-capabilities">
        {Object.entries(ic).map(([k, c]) => (
          <li key={k} className="rounded-md border border-line bg-chip px-2 py-1" data-input={k} data-status={c.status}>
            <span className="font-medium">{CAP_LABEL[k] ?? humanise(k)}:</span> {humanise(c.status).toLowerCase()}
            {c.reason ? <span className="text-subtle"> · {c.reason}</span> : c.note ? <span className="text-subtle"> · {c.note}</span> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function CheckMessage() {
  usePageTitle("Check a message or link");
  const role = useAccountRole();
  const nav = useNavigate();
  // the message lives only in this component's memory: never in storage, the URL or logs
  const [v, setV] = useState<CheckValues>(EMPTY_CHECK);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ msg: string; unavailable: boolean } | null>(null);
  const [result, setResult] = useState<CheckResult | null>(null);
  const [checked, setChecked] = useState<CheckValues | null>(null);
  const [announce, setAnnounce] = useState("");
  const formRef = useRef<HTMLFormElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  const set = <K extends keyof CheckValues>(k: K, val: CheckValues[K]) => {
    setV((p) => ({ ...p, [k]: val }));
    if (errors[k]) setErrors((e) => ({ ...e, [k]: "" }));
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const errs = validateCheck(v);
    setErrors(errs);
    if (Object.values(errs).some(Boolean)) {
      setTimeout(() => formRef.current?.querySelector<HTMLElement>("[aria-invalid='true']")?.focus(), 0);
      return;
    }
    setBusy(true);
    setAnnounce("Checking…");
    try {
      const r = await complaintsApi.check(buildCheck(v));
      setResult(r);
      setChecked(v);
      setAnnounce(`Check finished: ${r.risk_level ? `${r.risk_level.toLowerCase()} risk` : "no risk level given"}. Nothing was stored.`);
      setTimeout(() => resultRef.current?.focus(), 0);
    } catch (err) {
      setAnnounce("");
      if (err instanceof ComplaintsApiError && err.status === 422) {
        setErrors(err.fieldErrors);
        setError({ msg: err.formErrors[0] ?? "Some details need attention. Check the fields marked below.", unavailable: false });
      } else {
        const unavailable = err instanceof ComplaintsApiError && (err.status === 0 || err.status >= 500);
        setError({ msg: messageFor(err), unavailable });
      }
    } finally {
      setBusy(false);
    }
  };

  const register = () => {
    if (!checked) return;
    nav("/me/complaints/new", {
      state: {
        prefill: {
          description: checked.text.trim(),
          suspicious_url: checked.url.trim(),
          upi_id: checked.upi_id.trim(),
          suspect_phone: checked.phone.trim(),
          suspect_email: checked.email.trim(),
          amount: checked.amount.trim(),
          payment_method: checked.payment_method,
        },
      },
    });
  };

  const opt = (k: Exclude<keyof CheckValues, "text" | "payment_method">, label: string, hint: string, extra: { inputMode?: "url" | "tel" | "email" | "decimal"; type?: string } = {}) => (
    <Field label={label} optional hint={hint} error={errors[k]}>
      {(p) => <input {...p} type={extra.type ?? "text"} inputMode={extra.inputMode} value={v[k]} onChange={(e) => set(k, e.target.value)} className={inputCls} autoComplete="off" data-testid={`chk-${k}`} disabled={busy} />}
    </Field>
  );

  return (
    <div className="mx-auto max-w-4xl space-y-5 px-4 py-6 md:px-6">
      <PageHeader
        title="Check a message or link"
        subtitle="Paste a message, chat or call notes. It is checked by rules and models running on this server; nothing is stored and nothing is looked up on the internet."
      />
      <p className="sr-only" role="status" aria-live="polite">
        {announce}
      </p>
      <form ref={formRef} onSubmit={(e) => void submit(e)} noValidate className="panel space-y-4 p-4" data-testid="check-form">
        <Field label="Message or conversation" hint={`${MIN_TEXT}–${MAX_TEXT} characters. Remove anything you do not want checked.`} error={errors.text}>
          {(p) => (
            <textarea
              {...p}
              value={v.text}
              onChange={(e) => set("text", e.target.value)}
              rows={7}
              maxLength={MAX_TEXT + 200}
              className={inputCls}
              data-testid="chk-text"
              disabled={busy}
            />
          )}
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          {opt("url", "Link", "The page is not opened; only the address is checked.", { inputMode: "url" })}
          {opt("upi_id", "UPI ID", "Format check only.")}
          {opt("phone", "Phone number", "Format check only.", { inputMode: "tel", type: "tel" })}
          {opt("email", "Email address", "Format check only.", { inputMode: "email" })}
          {opt("amount", "Amount asked for (INR)", "Context only; does not change the score.", { inputMode: "decimal" })}
          <Field label="Payment method" optional hint="Context only." error={errors.payment_method}>
            {(p) => (
              <select {...p} value={v.payment_method} onChange={(e) => set("payment_method", e.target.value as PaymentMethod | "")} className={inputCls} disabled={busy} data-testid="chk-payment_method">
                <option value="">Not provided</option>
                {PAYMENT_METHODS.map((m) => (
                  <option key={m} value={m}>
                    {PAYMENT_LABEL[m]}
                  </option>
                ))}
              </select>
            )}
          </Field>
        </div>
        <p className="flex items-start gap-1.5 text-xs text-subtle">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span data-testid="chk-attachments-note">
            This check takes text and the optional details only. Call recordings are analysed when you attach them to a complaint: they are transcribed on this server when the complaint analysis runs. Screenshots are not analysed: no OCR (text-recognition) engine is installed.
          </span>
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" disabled={busy} data-testid="chk-submit">
            {busy ? <Spinner /> : <ScanSearch className="h-4 w-4" aria-hidden />}
            {busy ? "Checking…" : "Check"}
          </Button>
          {(result || v !== EMPTY_CHECK) && !busy && (
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setV(EMPTY_CHECK);
                setResult(null);
                setChecked(null);
                setErrors({});
                setError(null);
              }}
            >
              Clear
            </Button>
          )}
        </div>
      </form>

      {error && (error.unavailable ? <ErrorBlock message={error.msg} unavailable onRetry={() => formRef.current?.requestSubmit()} /> : (
        <p role="alert" className="rounded-lg border border-danger/40 bg-danger-soft p-3 text-sm" data-testid="check-error">
          {error.msg}
        </p>
      ))}

      {result && (
        <div ref={resultRef} tabIndex={-1} className="outline-none">
          <Panel title="Result" icon={<ScanSearch className="h-3.5 w-3.5" aria-hidden />} data-testid="check-result">
            <div className="space-y-4 p-4">
              <p className="flex items-center gap-1.5 rounded-lg border border-safe/40 bg-safe-soft px-3 py-2 text-xs" data-testid="nothing-stored">
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-safe" aria-hidden />
                Nothing was stored: this check is not a complaint and is not saved. Checked {formatDateTime(result.created_at)}.
              </p>
              <AnalysisResultView a={result} idPrefix="chk" footer={<InputCapabilities r={result} />} />
              {role === "CITIZEN" && (
                <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
                  <Button variant="intel" onClick={register} data-testid="chk-register">
                    <FilePlus2 className="h-4 w-4" aria-hidden /> Register a complaint with this text
                  </Button>
                  <span className="text-xs text-subtle">Opens the complaint form filled in; you review it and choose a category before submitting.</span>
                </div>
              )}
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}
