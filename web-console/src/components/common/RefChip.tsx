import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { shortRef } from "@/lib/format";
import { cn } from "@/lib/utils/cn";

/** Opaque ref shown as a short mono chip; click copies the full value. */
export function RefChip({ value, n = 8, className }: { value: string; n?: number; className?: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setDone(true);
      setTimeout(() => setDone(false), 1200);
    } catch {
      /* clipboard unavailable */
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      title="Copy full reference"
      aria-label={`Copy reference ${shortRef(value, n)}`}
      className={cn(
        "mono inline-flex items-center gap-1 rounded-md border border-line bg-chip px-1.5 py-0.5 text-[11px] text-muted transition-colors hover:text-fg",
        className,
      )}
    >
      {shortRef(value, n)}
      {done ? <Check className="h-3 w-3 text-safe" aria-hidden /> : <Copy className="h-3 w-3 opacity-60" aria-hidden />}
    </button>
  );
}
