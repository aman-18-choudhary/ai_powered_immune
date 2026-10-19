import { useState, type FormEvent } from "react";
import { PencilLine } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Field, Spinner, inputCls } from "./ui";
import { ComplaintsApiError, complaintsApi, messageFor } from "@/lib/complaints/api";
import { PAYMENT_LABEL } from "@/lib/complaints/format";
import { PAYMENT_METHODS, type ComplaintDetail } from "@/lib/complaints/types";
import { cn } from "@/lib/utils/cn";

/** The optional details a complainant may add or correct while the complaint is SUBMITTED. */
const TEXT_FIELDS = [
  ["upi_id", "UPI ID the money went to"],
  ["utr_reference", "UTR / reference"],
  ["amount", "Amount (INR)"],
  ["bank_name", "Bank name"],
  ["bank_account", "Bank account (scammer's)"],
  ["ifsc", "IFSC"],
  ["suspect_phone", "Scammer's phone"],
  ["suspect_email", "Scammer's email"],
  ["suspicious_url", "Suspicious link"],
  ["social_platform", "Social platform"],
  ["social_account", "Social account"],
  ["location_text", "Locality"],
  ["city", "City"],
  ["state", "State"],
  ["latitude", "Latitude"],
  ["longitude", "Longitude"],
] as const;

type Key = (typeof TEXT_FIELDS)[number][0] | "payment_method";

function initial(c: ComplaintDetail): Record<Key, string> {
  const out = {} as Record<Key, string>;
  for (const [k] of TEXT_FIELDS) {
    const v = (c as unknown as Record<string, unknown>)[k];
    out[k] = v === null || v === undefined ? "" : String(v);
  }
  out.payment_method = c.payment_method ?? "";
  return out;
}

export function EditDetails({ complaint, onSaved }: { complaint: ComplaintDetail; onSaved: (c: ComplaintDetail) => void }) {
  const [open, setOpen] = useState(false);
  const [v, setV] = useState<Record<Key, string>>(() => initial(complaint));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const start = () => {
    setV(initial(complaint));
    setError(null);
    setFieldErrors({});
    setOpen(true);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const before = initial(complaint);
    const changes: Record<string, string | null> = {};
    for (const k of Object.keys(v) as Key[]) if (v[k].trim() !== before[k]) changes[k] = v[k].trim() === "" ? null : v[k].trim();
    // coordinates travel together
    if ("latitude" in changes || "longitude" in changes) {
      changes.latitude = v.latitude.trim() || null;
      changes.longitude = v.longitude.trim() || null;
    }
    if (Object.keys(changes).length === 0) return setError("Nothing was changed.");
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      onSaved(await complaintsApi.editDetails(complaint.id, changes));
      setOpen(false);
    } catch (err) {
      setError(messageFor(err));
      if (err instanceof ComplaintsApiError) setFieldErrors(err.fieldErrors);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button size="sm" variant="secondary" onClick={start} data-testid="edit-details">
        <PencilLine className="h-3.5 w-3.5" aria-hidden /> Add or correct details
      </Button>
      <Dialog open={open} onOpenChange={(o) => !busy && setOpen(o)}>
        <DialogContent className="max-h-[90vh] w-[min(94vw,640px)] overflow-y-auto">
          <DialogTitle className="text-lg font-semibold">Add or correct details</DialogTitle>
          <DialogDescription className="mt-1 text-sm text-muted">
            Possible until review starts. The category, title and description stay as you filed them. Leave a box empty to remove what it held.
          </DialogDescription>
          <form onSubmit={(e) => void submit(e)} className="mt-4 grid gap-3 sm:grid-cols-2" noValidate data-testid="edit-form">
            <Field label="Payment method" optional error={fieldErrors.payment_method}>
              {(p) => (
                <select {...p} value={v.payment_method} onChange={(e) => setV((x) => ({ ...x, payment_method: e.target.value }))} className={inputCls} disabled={busy} data-testid="e-payment_method">
                  <option value="">Not provided</option>
                  {PAYMENT_METHODS.map((m) => (
                    <option key={m} value={m}>
                      {PAYMENT_LABEL[m]}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            {TEXT_FIELDS.map(([k, label]) => (
              <Field key={k} label={label} optional error={fieldErrors[k]}>
                {(p) => <input {...p} value={v[k]} onChange={(e) => setV((x) => ({ ...x, [k]: e.target.value }))} className={inputCls} disabled={busy} autoComplete="off" data-testid={`e-${k}`} />}
              </Field>
            ))}
            {error && (
              <p role="alert" className={cn("rounded-lg border border-danger/40 bg-danger-soft p-2 text-sm sm:col-span-2")} data-testid="edit-error">
                {error}
              </p>
            )}
            <div className="flex justify-end gap-2 sm:col-span-2">
              <Button type="button" variant="secondary" onClick={() => setOpen(false)} disabled={busy}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" disabled={busy} data-testid="edit-save">
                {busy && <Spinner />} Save details
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
