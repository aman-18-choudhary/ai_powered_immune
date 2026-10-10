import { Check, Scale, X } from "lucide-react";
import { Panel } from "@/components/common/Panel";

const PROVES = [
  "Entries were not changed or removed after they were chained: any edit breaks every later hash.",
  "The signed checkpoint binds the chain head to the ledger's key at the moment it was signed.",
  "A redacted entry's payload hash is still bound into the chain, so withholding is visible, not silent.",
];
const NOT = [
  "When something truly happened: timestamps are the ledger's receipt time, not the event time (and here a simulated clock).",
  "That the key is the right one: pinning a public key out of band is what turns integrity into authorship.",
  "That the contents are true: the ledger records what services reported, simulator data in this demo.",
  "Legal admissibility. This is not legal advice; admissibility rules (for example a section 63 certificate) are separate.",
];

export function LimitsPanel() {
  return (
    <Panel title="What the chain proves, and what it does not" icon={<Scale className="h-3.5 w-3.5" aria-hidden />} bodyClassName="grid gap-4 p-4 md:grid-cols-2" data-testid="limits">
      <div>
        <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-safe">
          <Check className="h-4 w-4" aria-hidden /> Proves
        </h3>
        <ul className="space-y-2 text-sm">
          {PROVES.map((t) => (
            <li key={t} className="flex gap-2">
              <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-safe" aria-hidden />
              <span>{t}</span>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-warn">
          <X className="h-4 w-4" aria-hidden /> Does not prove
        </h3>
        <ul className="space-y-2 text-sm">
          {NOT.map((t) => (
            <li key={t} className="flex gap-2">
              <X className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" aria-hidden />
              <span>{t}</span>
            </li>
          ))}
        </ul>
      </div>
    </Panel>
  );
}
