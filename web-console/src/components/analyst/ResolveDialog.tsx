import { useEffect, useId, useState } from "react";
import { ShieldAlert, Undo2 } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { formatInr } from "@/lib/format";
import { NOTE_MAX, validateNote, type ResolveAction } from "@/lib/analyst/queue";
import type { HoldView } from "@/types/api";

interface Props {
  hold: HoldView | null;
  action: ResolveAction | null;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: (note: string) => void;
}

const COPY: Record<ResolveAction, { title: string; verb: string; blurb: string; variant: "danger" | "safe" }> = {
  confirm_block: {
    title: "Confirm mule and block",
    verb: "Confirm mule",
    blurb: "The transfer is blocked and an antibody is published so other banks stop the same receiver.",
    variant: "danger",
  },
  release: {
    title: "Release the hold",
    verb: "Release",
    blurb: "The transfer goes through and no antibody is published. Use only when the payer verified it.",
    variant: "safe",
  },
};

export function ResolveDialog({ hold, action, busy, onCancel, onConfirm }: Props) {
  const [note, setNote] = useState("");
  const [touched, setTouched] = useState(false);
  const id = useId();
  useEffect(() => {
    setNote("");
    setTouched(false);
  }, [hold?.txn_ref, action]);
  const v = validateNote(note);
  const open = !!hold && !!action;
  const c = action ? COPY[action] : COPY.release;
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent aria-describedby={`${id}-d`} className="w-[min(94vw,520px)]">
        <DialogTitle className="flex items-center gap-2 text-lg font-semibold">
          {action === "release" ? <Undo2 className="h-5 w-5 text-safe" aria-hidden /> : <ShieldAlert className="h-5 w-5 text-danger-text" aria-hidden />}
          {c.title}
        </DialogTitle>
        <DialogDescription id={`${id}-d`} className="mt-1 text-sm text-muted">
          {c.blurb}
        </DialogDescription>
        {hold && (
          <dl className="mt-4 grid grid-cols-2 gap-2 rounded-xl border border-line bg-chip p-3 text-xs">
            <dt className="text-muted">Amount</dt>
            <dd className="mono text-right">{formatInr(hold.amount_inr)}</dd>
            <dt className="text-muted">Rail</dt>
            <dd className="text-right">{hold.rail}</dd>
            <dt className="text-muted">Decision</dt>
            <dd className="mono text-right">
              {hold.decision} (seq {hold.decision_seq})
            </dd>
          </dl>
        )}
        <form
          className="mt-4 space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            setTouched(true);
            if (v.ok) onConfirm(v.note);
          }}
        >
          <label htmlFor={`${id}-n`} className="text-sm font-medium">
            Reason note <span className="text-danger-text">(required)</span>
          </label>
          <textarea
            id={`${id}-n`}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onBlur={() => setTouched(true)}
            rows={3}
            maxLength={NOTE_MAX + 40}
            aria-invalid={touched && !v.ok}
            aria-describedby={`${id}-h`}
            placeholder="Why this decision? (no phone, account or card numbers)"
            className="w-full rounded-xl border border-line-strong bg-sunken p-3 text-sm outline-none focus-visible:border-intel"
            data-testid="resolve-note"
          />
          <div id={`${id}-h`} className="flex items-start justify-between gap-3 text-xs">
            <span className={touched && !v.ok ? "text-danger-text" : "text-subtle"} role={touched && !v.ok ? "alert" : undefined}>
              {touched && !v.ok ? v.message : "Kept in this browser as a local audit note. The resolve API has no note field, so it is not sent."}
            </span>
            <span className="mono tnum shrink-0 text-subtle">
              {note.trim().length}/{NOTE_MAX}
            </span>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
            <Button type="submit" variant={c.variant} disabled={busy || (touched && !v.ok)} data-testid="resolve-submit">
              {busy ? "Working…" : c.verb}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
