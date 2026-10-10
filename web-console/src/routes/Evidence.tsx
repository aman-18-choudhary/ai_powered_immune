import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BookOpenCheck, Clock, Flag, KeyRound, Link2 } from "lucide-react";
import { EmptyState, Panel, Skeleton } from "@/components/common/Panel";
import { RefChip } from "@/components/common/RefChip";
import { ChainView } from "@/components/evidence/ChainView";
import { EntryInspector } from "@/components/evidence/EntryInspector";
import { CasePanel } from "@/components/evidence/CasePanel";
import { LimitsPanel } from "@/components/evidence/LimitsPanel";
import { useRest } from "@/lib/hooks/useRest";
import { usePageTitle } from "@/lib/hooks/usePageTitle";
import { endpoints } from "@/lib/api/endpoints";
import { defaultCaseRefs, parseCheckpoints, parseLedgerPage, type LedgerPage } from "@/lib/evidence/ledger";
import { useRunStore } from "@/state/runStore";
import { useEvidence } from "@/state/evidence";
import type { LedgerHead } from "@/types/api";

async function loadAll(signal: AbortSignal): Promise<{ page: LedgerPage; head: LedgerHead | null; checkpoints: ReturnType<typeof parseCheckpoints> }> {
  const items: LedgerPage["items"] = [];
  let from = 1;
  let clock = false;
  for (let i = 0; i < 10; i++) {
    const text = await endpoints.ledgerEntriesText(from, 200, signal);
    const p = parseLedgerPage(text);
    items.push(...p.items);
    clock = clock || p.simulated_clock;
    if (p.next_from_seq === null || p.items.length === 0) break;
    from = p.next_from_seq;
  }
  const [head, cps] = await Promise.all([
    endpoints.ledgerHead(signal).catch(() => null),
    endpoints.checkpoints(signal).then(parseCheckpoints).catch(() => []),
  ]);
  return { page: { items, next_from_seq: null, simulated_clock: clock }, head, checkpoints: cps };
}

export default function Evidence() {
  usePageTitle("Evidence");
  const streamSeq = useRunStore((s) => s.run.ledger.reduce((m, l) => Math.max(m, l.seq), 0));
  const runId = useRunStore((s) => s.run.runId);
  const rest = useRest((s) => loadAll(s), `${runId}:${streamSeq}`, 350);
  const verify = useEvidence((s) => s.verify);
  const [selected, setSelected] = useState<number | null>(null);
  const seen = useRef<Set<number> | null>(null);
  const [fresh, setFresh] = useState<Set<number>>(new Set());

  const entries = useMemo(() => rest.data?.page.items ?? [], [rest.data]);
  useEffect(() => {
    if (!rest.data) return;
    const now = new Set(entries.map((e) => e.seq));
    if (seen.current === null) {
      seen.current = now;
      return;
    }
    const added = new Set([...now].filter((s) => !seen.current!.has(s)));
    seen.current = now;
    if (added.size > 0) {
      setFresh(added);
      const h = setTimeout(() => setFresh(new Set()), 2500);
      return () => clearTimeout(h);
    }
  }, [rest.data, entries]);

  const redacted = useMemo(() => new Set(verify?.redacted_seqs ?? []), [verify]);
  const refs = useMemo(() => defaultCaseRefs(entries), [entries]);
  const entry = entries.find((e) => e.seq === selected) ?? null;
  const onVerified = useCallback(() => undefined, []);
  const head = rest.data?.head ?? null;
  const cps = rest.data?.checkpoints ?? [];

  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-3 px-4 py-4 md:px-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Evidence</h1>
          <p className="text-sm text-muted">An append-only hash chain you can verify without trusting this server.</p>
        </div>
        <p className="flex items-center gap-1.5 rounded-lg border border-warn/40 bg-warn-soft px-2.5 py-1 text-xs" data-testid="clock-note">
          <Clock className="h-3.5 w-3.5 text-warn" aria-hidden />
          Simulated clock: ledger timestamps count one second per entry from 2026-01-01, unrelated to the run clock.
        </p>
      </header>

      <Panel
        title="Hash chain"
        icon={<Link2 className="h-3.5 w-3.5" aria-hidden />}
        actions={
          head && (
            <span className="mono tnum text-[11px] text-muted" data-testid="chain-head">
              {head.count} entries · head #{head.seq}
            </span>
          )
        }
      >
        {rest.loading && rest.data === null ? (
          <div className="flex gap-4 p-4" aria-busy="true" aria-label="Loading ledger">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-28 w-44" />
            ))}
          </div>
        ) : rest.error && rest.data === null ? (
          <EmptyState title={rest.forbidden ? "This role cannot read the ledger" : "Ledger unavailable"} hint={rest.error}>
            <button type="button" onClick={rest.reload} className="mt-2 rounded-lg border border-line-strong px-3 py-1 text-xs hover:bg-hover">
              Try again
            </button>
          </EmptyState>
        ) : entries.length === 0 ? (
          <EmptyState
            icon={<BookOpenCheck className="h-5 w-5" aria-hidden />}
            title="The chain is empty"
            hint="Entries appear as the scenario runs: start it from the Live page."
          />
        ) : (
          <>
            <ChainView entries={entries} checkpoints={cps} redacted={redacted} freshSeqs={fresh} selected={selected} onInspect={setSelected} />
            <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-line px-4 py-2 text-[11px] text-muted">
              <span className="inline-flex items-center gap-1">
                <span className="h-0.5 w-5 bg-flow" aria-hidden /> prev_hash links to the block before
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="h-3 w-5 rounded border border-dashed border-line-strong bg-[repeating-linear-gradient(135deg,transparent_0_3px,var(--border)_3px_4px)]" aria-hidden /> payload withheld in the package (verify to mark these)
              </span>
              <span className="inline-flex items-center gap-1">
                <Flag className="h-3 w-3 text-intel" aria-hidden /> signed checkpoint
              </span>
            </div>
          </>
        )}
      </Panel>

      <div className="grid gap-3 lg:grid-cols-[1.35fr_1fr]">
        <CasePanel defaultRefs={refs} onVerified={onVerified} />
        <div className="space-y-3">
          <Panel title="Signing key and checkpoints" icon={<KeyRound className="h-3.5 w-3.5" aria-hidden />} bodyClassName="space-y-3 p-4">
            {head ? (
              <dl className="grid grid-cols-[84px_1fr] gap-x-2 gap-y-1 text-xs">
                <dt className="text-muted">Algorithm</dt>
                <dd>{head.algorithm}</dd>
                <dt className="text-muted">Key id</dt>
                <dd className="mono">{head.key_id}</dd>
                <dt className="text-muted">Public key</dt>
                <dd>
                  <RefChip value={head.public_key_hex} n={16} />
                </dd>
                <dt className="text-muted">Head hash</dt>
                <dd>
                  <RefChip value={head.entry_hash} n={16} />
                </dd>
              </dl>
            ) : (
              <p className="text-xs text-muted">Head not available.</p>
            )}
            <ul className="space-y-1.5" aria-label="Signed checkpoints" data-testid="checkpoints">
              {cps.length === 0 && <li className="text-xs text-subtle">No signed checkpoint yet.</li>}
              {cps.map((c) => (
                <li key={c.checkpoint_id} className="flex items-center gap-2 rounded-lg border border-line bg-chip px-2 py-1.5 text-xs">
                  <Flag className="h-3.5 w-3.5 shrink-0 text-intel" aria-hidden />
                  <span className="mono">{c.checkpoint_id}</span>
                  <span className="text-muted">covers #1 to #{c.seq}</span>
                </li>
              ))}
            </ul>
          </Panel>
          <LimitsPanel />
        </div>
      </div>

      <EntryInspector entry={entry} sealedInPackage={!!entry && redacted.has(entry.seq)} onClose={() => setSelected(null)} />
    </div>
  );
}
