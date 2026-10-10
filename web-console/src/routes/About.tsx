import { useState } from "react";
import { AlertTriangle, BookOpen, Boxes, Check, Copy, Package, Play, Scale, Terminal } from "lucide-react";
import { Panel } from "@/components/common/Panel";
import { ArchDiagram } from "@/components/about/ArchDiagram";
import { AUDIT_SUMMARY, LIMITS, REAL_VS_SIMULATED, STACK } from "@/lib/about/architecture";
import { usePageTitle } from "@/lib/hooks/usePageTitle";
import pkg from "../../package.json";

const RUN = `make demo      # builds the console, serves it with the demo server on :8080
# or, for development with hot reload:
make demo-server    # backend on :8080
make console-dev    # console on :5173`;

/** Libraries named in package.json at build time (versions as declared). */
export function declaredLibraries(): { name: string; version: string }[] {
  const deps = { ...(pkg.dependencies as Record<string, string>) };
  return Object.entries(deps)
    .map(([name, version]) => ({ name, version: version.replace(/^[\^~]/, "") }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

function CopyBlock({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="relative">
      <pre tabIndex={0} aria-label="Commands to run the demo" className="mono overflow-x-auto rounded-xl border border-line bg-sunken p-3 pr-12 text-[12.5px] leading-relaxed" data-testid="run-block">
        {text}
      </pre>
      <button
        type="button"
        aria-label="Copy commands"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setDone(true);
            setTimeout(() => setDone(false), 1400);
          } catch {
            /* clipboard unavailable */
          }
        }}
        className="absolute right-2 top-2 rounded-lg border border-line bg-chip p-1.5 text-muted hover:text-fg"
      >
        {done ? <Check className="h-4 w-4 text-safe" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}
      </button>
    </div>
  );
}

export default function About() {
  usePageTitle("About");
  const libs = declaredLibraries();
  return (
    <div className="mx-auto w-full max-w-[1200px] space-y-4 px-4 py-4 md:px-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">About ScamShield Immune</h1>
        <p className="max-w-3xl text-sm text-muted">
          One platform that lets a second bank block a mule account seconds after the first bank&apos;s analyst confirms it, with an audit trail an outsider can verify. This page says plainly what is real and what is made up.
        </p>
      </header>

      <Panel title="Architecture" icon={<Boxes className="h-3.5 w-3.5" aria-hidden />} bodyClassName="p-4">
        <ArchDiagram />
      </Panel>

      <Panel title="Real versus simulated" icon={<Scale className="h-3.5 w-3.5" aria-hidden />} bodyClassName="overflow-x-auto p-0">
        <table className="w-full min-w-[720px] text-left text-sm" data-testid="real-table">
          <caption className="sr-only">What runs real service code and what is synthetic</caption>
          <thead className="text-[11px] uppercase tracking-wider text-subtle">
            <tr className="border-b border-line">
              <th scope="col" className="px-4 py-2 font-semibold">Piece</th>
              <th scope="col" className="px-3 py-2 font-semibold">Runs real service code</th>
              <th scope="col" className="px-4 py-2 font-semibold">Synthetic or simplified</th>
            </tr>
          </thead>
          <tbody>
            {REAL_VS_SIMULATED.map((r) => (
              <tr key={r.piece} className="border-b border-line align-top last:border-0">
                <th scope="row" className="px-4 py-3 font-semibold">{r.piece}</th>
                <td className="px-3 py-3 text-muted">{r.real}</td>
                <td className="px-4 py-3 text-muted">{r.simulated}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Honest limits" icon={<AlertTriangle className="h-3.5 w-3.5 text-warn" aria-hidden />} className="border-warn/40" bodyClassName="p-4">
          <ul className="space-y-2.5 text-sm" data-testid="limits-list">
            {LIMITS.map((l) => (
              <li key={l} className="flex gap-2">
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-warn" aria-hidden />
                <span>{l}</span>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title="How to run it" icon={<Terminal className="h-3.5 w-3.5" aria-hidden />} bodyClassName="space-y-3 p-4">
          <CopyBlock text={RUN} />
          <p className="flex items-center gap-2 text-sm text-muted">
            <Play className="h-4 w-4 text-flow" aria-hidden /> Then open the page, choose a role and press Play on the Live page.
          </p>
        </Panel>
      </div>

      <Panel title="The audit trail in short" icon={<BookOpen className="h-3.5 w-3.5" aria-hidden />} bodyClassName="grid gap-3 p-4 md:grid-cols-2">
        {AUDIT_SUMMARY.map((a) => (
          <div key={a.title} className="rounded-xl border border-line bg-chip p-3">
            <h3 className="text-sm font-semibold">{a.title}</h3>
            <p className="mt-1 text-sm text-muted">{a.body}</p>
          </div>
        ))}
        <p className="text-xs text-subtle md:col-span-2">Summarised from docs/audit-trail.md in the repository. No claim about legal admissibility is made.</p>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Tech stack" icon={<Boxes className="h-3.5 w-3.5" aria-hidden />} bodyClassName="space-y-3 p-4">
          {STACK.map((g) => (
            <div key={g.group}>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-subtle">{g.group}</h3>
              <ul className="mt-1.5 flex flex-wrap gap-1.5">
                {g.items.map((i) => (
                  <li key={i} className="rounded-md border border-line bg-chip px-2 py-0.5 text-xs">{i}</li>
                ))}
              </ul>
            </div>
          ))}
        </Panel>
        <Panel title="Credits" icon={<Package className="h-3.5 w-3.5" aria-hidden />} bodyClassName="p-4">
          <p className="mb-2 text-xs text-muted">Console libraries, listed from package.json at build time. Fonts: Inter and JetBrains Mono (SIL OFL). India outlines: geojson-india (MIT).</p>
          <ul className="mono grid grid-cols-1 gap-x-4 gap-y-0.5 text-[11.5px] sm:grid-cols-2" data-testid="credits">
            {libs.map((l) => (
              <li key={l.name} className="flex justify-between gap-2">
                <span className="truncate">{l.name}</span>
                <span className="text-subtle">{l.version}</span>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  );
}
