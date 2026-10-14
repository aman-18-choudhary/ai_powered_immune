import { useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft, Crosshair, FilePlus2, Paperclip, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Field, PageHeader, Spinner, inputCls } from "@/components/complaints/ui";
import { UploadList } from "@/components/complaints/UploadList";
import { ComplaintsApiError, complaintsApi, messageFor } from "@/lib/complaints/api";
import { ACCEPT, EMPTY_FORM, MAX_FILES, buildPayload, newIdempotencyKey, validate, type FormValues } from "@/lib/complaints/form";
import { CATEGORY_LABEL, PAYMENT_LABEL, formatBytes } from "@/lib/complaints/format";
import { CATEGORIES, PAYMENT_METHODS, type Category, type PaymentMethod } from "@/lib/complaints/types";
import { uploadSequential, type UploadItem } from "@/lib/complaints/upload";
import { usePageTitle } from "@/lib/hooks/usePageTitle";
import { cn } from "@/lib/utils/cn";

type Geo = { k: "idle" } | { k: "asking" } | { k: "ok" } | { k: "error"; msg: string };

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <fieldset className="panel space-y-3 p-4">
      <legend className="sr-only">{title}</legend>
      <div aria-hidden>
        <h2 className="text-sm font-semibold">{title}</h2>
        {hint && <p className="text-xs text-subtle">{hint}</p>}
      </div>
      {children}
    </fieldset>
  );
}

export default function NewComplaint() {
  usePageTitle("Register a complaint");
  const nav = useNavigate();
  const [v, setV] = useState<FormValues>(EMPTY_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [phase, setPhase] = useState<"form" | "creating" | "uploading">("form");
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [geo, setGeo] = useState<Geo>({ k: "idle" });
  const idem = useRef(newIdempotencyKey());
  const formRef = useRef<HTMLFormElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const busy = phase !== "form";

  const set = <K extends keyof FormValues>(k: K, val: FormValues[K]) => {
    setV((p) => ({ ...p, [k]: val }));
    if (errors[k]) setErrors((e) => ({ ...e, [k]: "" }));
  };

  const focusFirstError = () =>
    setTimeout(() => formRef.current?.querySelector<HTMLElement>("[aria-invalid='true']")?.focus(), 0);

  const locate = () => {
    if (!("geolocation" in navigator)) {
      setGeo({ k: "error", msg: "This browser cannot share a location. You can type the coordinates or leave them empty." });
      return;
    }
    setGeo({ k: "asking" });
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setV((p) => ({ ...p, latitude: pos.coords.latitude.toFixed(5), longitude: pos.coords.longitude.toFixed(5) }));
        setGeo({ k: "ok" });
      },
      (err) => {
        const msg =
          err.code === err.PERMISSION_DENIED
            ? "Location permission was denied. You can allow it in the browser's address bar, type the coordinates, or leave them empty."
            : err.code === err.TIMEOUT
              ? "Getting your location took too long. Try again or type the coordinates."
              : "Your location is not available right now. You can type the coordinates or leave them empty.";
        setGeo({ k: "error", msg });
      },
      { enableHighAccuracy: false, timeout: 15000, maximumAge: 60000 },
    );
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setFormError(null);
    const errs = validate(v);
    setErrors(errs);
    if (Object.keys(errs).length > 0) {
      setFormError(`Please fix ${Object.keys(errs).length === 1 ? "1 field" : `${Object.keys(errs).length} fields`} marked below.`);
      focusFirstError();
      return;
    }
    setPhase("creating");
    let created;
    try {
      created = await complaintsApi.create(buildPayload(v), idem.current);
    } catch (err) {
      setPhase("form");
      if (err instanceof ComplaintsApiError && err.status === 422) {
        const fe = { ...err.fieldErrors };
        setErrors(fe);
        setFormError(err.formErrors[0] ?? "The service did not accept some details. Check the fields marked below.");
        focusFirstError();
      } else setFormError(messageFor(err));
      return;
    }
    let results: UploadItem[] = [];
    if (files.length > 0) {
      setPhase("uploading");
      results = await uploadSequential(created.id, files, setUploads);
    }
    idem.current = newIdempotencyKey();
    nav(`/me/complaints/${created.id}`, { state: { created: true, uploads: results } });
  };

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    setFiles((cur) => [...cur, ...Array.from(list)].slice(0, MAX_FILES));
    if (fileRef.current) fileRef.current.value = "";
  };

  const text = (k: keyof FormValues, label: string, o: { hint?: string; type?: string; autoComplete?: string; inputMode?: "numeric" | "decimal" | "email" | "tel" | "url"; placeholder?: string; className?: string } = {}) => (
    <Field label={label} optional hint={o.hint} error={errors[k]} className={o.className}>
      {(p) => (
        <input
          {...p}
          type={o.type ?? "text"}
          name={k}
          inputMode={o.inputMode}
          autoComplete={o.autoComplete ?? "off"}
          placeholder={o.placeholder}
          value={v[k]}
          onChange={(e) => set(k, e.target.value as FormValues[typeof k])}
          className={inputCls}
          disabled={busy}
          data-testid={`f-${k}`}
        />
      )}
    </Field>
  );

  return (
    <div className="mx-auto max-w-3xl space-y-5 px-4 py-6 md:px-6">
      <Link to="/me" className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
        <ArrowLeft className="h-4 w-4" aria-hidden /> My complaints
      </Link>
      <PageHeader
        title="Register a complaint"
        subtitle="Only the category, a title and a description are required. Add any other details you have; leave the rest empty. The reported time is set by the server when you submit."
      />
      <form ref={formRef} onSubmit={submit} noValidate className="space-y-4" aria-describedby={formError ? "form-error" : undefined}>
        {formError && (
          <p id="form-error" role="alert" className="rounded-xl border border-danger/40 bg-danger-soft p-3 text-sm" data-testid="form-error">
            {formError}
          </p>
        )}
        <Section title="What happened" hint="Required">
          <Field label="Category" error={errors.category}>
            {(p) => (
              <select {...p} required value={v.category} onChange={(e) => set("category", e.target.value as Category)} className={inputCls} disabled={busy} data-testid="f-category">
                <option value="">Choose a category…</option>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {CATEGORY_LABEL[c]}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label="Title" hint="A short summary, e.g. 'Caller claimed to be CBI and asked for money'." error={errors.title}>
            {(p) => <input {...p} required maxLength={200} value={v.title} onChange={(e) => set("title", e.target.value)} className={inputCls} disabled={busy} data-testid="f-title" />}
          </Field>
          <Field label="Description" hint={`What happened, in your own words (10 to 5,000 characters). ${v.description.trim().length}/5000`} error={errors.description}>
            {(p) => (
              <textarea {...p} required rows={6} maxLength={5000} value={v.description} onChange={(e) => set("description", e.target.value)} className={cn(inputCls, "resize-y")} disabled={busy} data-testid="f-description" />
            )}
          </Field>
        </Section>

        <Section title="When it happened" hint="Optional">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Incident date" optional error={errors.incident_at}>
              {(p) => <input {...p} type="date" value={v.incident_date} onChange={(e) => set("incident_date", e.target.value)} className={inputCls} disabled={busy} data-testid="f-incident_date" />}
            </Field>
            <Field label="Incident time" optional hint="Your local time.">
              {(p) => <input {...p} type="time" value={v.incident_time} onChange={(e) => set("incident_time", e.target.value)} className={inputCls} disabled={busy} data-testid="f-incident_time" />}
            </Field>
          </div>
        </Section>

        <Section title="Money and payment" hint="Optional. Add what you know; bank details are those of the account the money went to.">
          <div className="grid gap-3 sm:grid-cols-2">
            {text("amount", "Amount lost (INR)", { inputMode: "decimal", placeholder: "e.g. 25000" })}
            <Field label="Payment method" optional error={errors.payment_method}>
              {(p) => (
                <select {...p} value={v.payment_method} onChange={(e) => set("payment_method", e.target.value as PaymentMethod | "")} className={inputCls} disabled={busy} data-testid="f-payment_method">
                  <option value="">Not specified</option>
                  {PAYMENT_METHODS.map((m) => (
                    <option key={m} value={m}>
                      {PAYMENT_LABEL[m]}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            {text("upi_id", "UPI ID the money went to", { placeholder: "name@bank" })}
            {text("utr_reference", "UTR / transaction reference")}
            {text("bank_name", "Bank name")}
            {text("bank_account", "Bank account number", { inputMode: "numeric", hint: "6 to 18 digits." })}
            {text("ifsc", "IFSC", { placeholder: "e.g. SBIN0001234" })}
          </div>
        </Section>

        <Section title="About the scammer" hint="Optional">
          <div className="grid gap-3 sm:grid-cols-2">
            {text("suspect_phone", "Phone number", { type: "tel", inputMode: "tel" })}
            {text("suspect_email", "Email address", { type: "email", inputMode: "email" })}
            {text("suspicious_url", "Suspicious link", { type: "url", inputMode: "url", placeholder: "https://…", className: "sm:col-span-2" })}
            {text("social_platform", "Social platform", { placeholder: "e.g. WhatsApp, Instagram" })}
            {text("social_account", "Social account / handle")}
          </div>
        </Section>

        <Section title="Where you are" hint="Optional. Used only as context; it does not change the analysis.">
          <div className="grid gap-3 sm:grid-cols-2">
            {text("location_text", "Locality or address", { className: "sm:col-span-2" })}
            {text("city", "City", { autoComplete: "address-level2" })}
            {text("state", "State", { autoComplete: "address-level1" })}
            {text("latitude", "Latitude", { inputMode: "decimal" })}
            {text("longitude", "Longitude", { inputMode: "decimal" })}
          </div>
          <div className="space-y-1.5">
            <Button type="button" size="sm" variant="secondary" onClick={locate} disabled={busy || geo.k === "asking"} data-testid="use-location">
              {geo.k === "asking" ? <Spinner /> : <Crosshair className="h-4 w-4" aria-hidden />} Use my location
            </Button>
            <p role="status" aria-live="polite" className="text-xs text-muted">
              {geo.k === "asking" && "Asking the browser for your location…"}
              {geo.k === "ok" && "Location filled in. Check it before submitting; you can clear it."}
            </p>
            {geo.k === "error" && (
              <p role="alert" className="text-xs text-danger-text" data-testid="geo-error">
                {geo.msg}
              </p>
            )}
            <p className="text-xs text-subtle">Your location is read only when you press the button.</p>
          </div>
        </Section>

        <Section title="Evidence files" hint="Optional. PNG, JPEG, WebP or PDF up to 10 MB; MP3, WAV, OGG or M4A up to 25 MB; at most 10 files. Uploaded after the complaint is created.">
          <div>
            <label htmlFor="evidence-input" className="inline-flex cursor-pointer items-center gap-2 rounded-xl border border-line-strong bg-chip px-3 py-2 text-sm font-medium hover:bg-hover">
              <Paperclip className="h-4 w-4" aria-hidden /> Add files <span className="text-xs font-normal text-subtle">(optional)</span>
            </label>
            <input id="evidence-input" ref={fileRef} type="file" multiple accept={ACCEPT} className="sr-only" onChange={(e) => addFiles(e.target.files)} disabled={busy} data-testid="f-evidence" />
          </div>
          {files.length > 0 && phase === "form" && (
            <ul className="space-y-1.5" aria-label="Selected files">
              {files.map((f, i) => (
                <li key={`${f.name}-${i}`} className="flex items-center justify-between gap-2 rounded-lg border border-line bg-chip px-3 py-1.5 text-sm">
                  <span className="min-w-0 truncate">
                    {f.name} <span className="text-xs text-subtle">({formatBytes(f.size)})</span>
                  </span>
                  <button type="button" onClick={() => setFiles((cur) => cur.filter((_, j) => j !== i))} className="rounded p-1 text-muted hover:text-fg" aria-label={`Remove ${f.name}`}>
                    <X className="h-4 w-4" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <UploadList items={uploads} />
        </Section>

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" size="lg" disabled={busy} data-testid="submit-complaint">
            {busy ? <Spinner /> : <FilePlus2 className="h-4 w-4" aria-hidden />}
            {phase === "creating" ? "Registering…" : phase === "uploading" ? "Uploading evidence…" : "Register complaint"}
          </Button>
          <p role="status" aria-live="polite" className="text-sm text-muted">
            {phase === "creating" && "Sending your complaint…"}
            {phase === "uploading" && "Complaint registered. Uploading evidence one file at a time…"}
          </p>
        </div>
      </form>
    </div>
  );
}
