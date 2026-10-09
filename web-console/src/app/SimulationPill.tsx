import { FlaskConical, Info } from "lucide-react";
import { Link } from "react-router-dom";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

export function SimulationPill() {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-full border border-warn/50 bg-[var(--pill-sim-bg)] px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wider text-[var(--pill-sim-fg)] hover:brightness-125"
          aria-label="Simulation. About these numbers"
        >
          <FlaskConical className="h-3.5 w-3.5" aria-hidden />
          Simulation
          <Info className="h-3 w-3 opacity-70" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(92vw,420px)]">
        <h3 className="mb-2 text-sm font-semibold">About these numbers</h3>
        <ul className="space-y-2 text-[13px] leading-snug text-muted">
          <li>
            <strong className="text-fg">Everything here is simulated.</strong> Calls, victims, mules and banks come from a
            simulator. No real person, account or transaction is involved.
          </li>
          <li>
            <strong className="text-fg">The hero case is constructed.</strong> It is a deliberately hard case: a seasoned
            mule account and a call-guard miss, so only the shared threat memory saves victim B.
          </li>
          <li>
            <strong className="text-fg">Benchmark figures are simulator-only.</strong> They are a regression and ablation
            harness on synthetic data, not real-world performance.
          </li>
          <li>
            <strong className="text-fg">Timings are real, data is not.</strong> Latencies such as antibody application are
            measured in this process; amounts and identities are synthetic.
          </li>
        </ul>
        <Link to="/about" className="mt-3 inline-block text-xs font-medium text-flow underline-offset-2 hover:underline">
          Read what is real vs simulated
        </Link>
      </PopoverContent>
    </Popover>
  );
}
