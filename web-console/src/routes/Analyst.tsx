import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Inbox, Keyboard, RefreshCw, ShieldCheck } from "lucide-react";
import { EmptyState, Panel, Skeleton } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { HoldRow, ROW_HEIGHT } from "@/components/analyst/HoldRow";
import { HoldDetail } from "@/components/analyst/HoldDetail";
import { ResolveDialog } from "@/components/analyst/ResolveDialog";
import { useRest } from "@/lib/hooks/useRest";
import { useVirtualList } from "@/lib/hooks/useVirtualList";
import { endpoints } from "@/lib/api/endpoints";
import { formatInr } from "@/lib/format";
import {
  explainResolveError, filterQueue, isOpen, sortQueue, withOptimistic, type Pending, type QueueFilter, type ResolveAction,
} from "@/lib/analyst/queue";
import { useRunStore } from "@/state/runStore";
import { useSession, canResolve } from "@/state/session";
import { useSimTime } from "@/state/useSimTime";
import { toast } from "@/state/toasts";
import type { HoldView } from "@/types/api";
import { usePageTitle } from "@/lib/hooks/usePageTitle";
import { cn } from "@/lib/utils/cn";

const VIEW_H = 560;
const FILTERS: { key: QueueFilter; label: string }[] = [
  { key: "open", label: "Open" },
  { key: "resolved", label: "Resolved" },
  { key: "all", label: "All" },
];

const SHORTCUTS: [string, string][] = [
  ["J / K", "Select next / previous hold"],
  ["C", "Confirm mule (asks for a reason note)"],
  ["R", "Release the hold (asks for a reason note)"],
  ["?", "Show this help"],
  ["Esc", "Close a dialog"],
];

export default function Analyst() {
  usePageTitle("Analyst Console");
  const role = useSession((s) => s.role);
  const canAct = canResolve(role);
  const run = useRunStore((s) => s.run);
  const simT = useSimTime();
  const [filter, setFilter] = useState<QueueFilter>("open");
  const [selected, setSelected] = useState<string | null>(null);
  const [pending, setPending] = useState<Record<string, Pending>>({});
  const [notes, setNotes] = useState<Record<string, { note: string; action: ResolveAction }>>({});
  const [patched, setPatched] = useState<Record<string, HoldView>>({});
  const [dialog, setDialog] = useState<{ ref: string; action: ResolveAction } | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [announce, setAnnounce] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  // Refetch whenever the stream changes any hold (state or decision), debounced by useRest.
  const sig = useMemo(
    () => run.holdOrder.map((k) => `${k}:${run.holds[k]?.state}:${run.holds[k]?.decision_seq}`).join("|") + `#${run.runId ?? ""}`,
    [run.holdOrder, run.holds, run.runId],
  );
  const rest = useRest((s) => endpoints.holds("all", s), sig, 250, true);
  useEffect(() => {
    setPatched({});
  }, [rest.data]);

  const server = useMemo(() => {
    const base = rest.data?.holds ?? [];
    return base.map((v) => patched[v.txn_ref] ?? v);
  }, [rest.data, patched]);
  const views = useMemo(() => sortQueue(withOptimistic(server, pending)), [server, pending]);
  const shown = useMemo(() => filterQueue(views, filter), [views, filter]);
  const openCount = useMemo(() => views.filter(isOpen).length, [views]);
  const overdueCount = useMemo(() => views.filter((h) => isOpen(h) && h.deadline_t <= simT).length, [views, simT]);

  // Keep a valid selection: the previous one if still listed, else the first row.
  useEffect(() => {
    if (shown.length === 0) {
      if (selected !== null) setSelected(null);
    } else if (!selected || !shown.some((h) => h.txn_ref === selected)) {
      setSelected(shown[0]?.txn_ref ?? null);
    }
  }, [shown, selected]);

  const current = shown.find((h) => h.txn_ref === selected) ?? null;
  const v = useVirtualList(shown.length, ROW_HEIGHT, VIEW_H);

  // Announce newly arrived open holds once.
  const seen = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const h of server) {
      if (!seen.current.has(h.txn_ref)) {
        seen.current.add(h.txn_ref);
        if (seen.current.size > 1 && isOpen(h)) setAnnounce(`New hold: ${h.rail} ${formatInr(h.amount_inr)}`);
      }
    }
  }, [server]);

  const move = useCallback(
    (dir: 1 | -1) => {
      if (shown.length === 0) return;
      const i = Math.max(0, shown.findIndex((h) => h.txn_ref === selected));
      const n = Math.min(shown.length - 1, Math.max(0, i + dir));
      const next = shown[n];
      if (!next) return;
      setSelected(next.txn_ref);
      const el = listRef.current;
      if (el) {
        const top = n * ROW_HEIGHT;
        if (top < el.scrollTop) el.scrollTop = top;
        else if (top + ROW_HEIGHT > el.scrollTop + VIEW_H) el.scrollTop = top + ROW_HEIGHT - VIEW_H;
      }
    },
    [shown, selected],
  );

  const openAction = useCallback(
    (a: ResolveAction) => {
      if (!canAct) {
        toast.info("Read-only role", "Sign in as Analyst or Admin to resolve holds.");
        return;
      }
      if (current && isOpen(current) && !pending[current.txn_ref]) setDialog({ ref: current.txn_ref, action: a });
    },
    [canAct, current, pending],
  );

  const resolve = useCallback(
    async (note: string) => {
      if (!dialog) return;
      const view = server.find((h) => h.txn_ref === dialog.ref);
      if (!view) return;
      const { ref, action } = dialog;
      setDialog(null);
      setPending((p) => ({ ...p, [ref]: { action, before: view } }));
      try {
        const r = await endpoints.resolveHold(ref, action, view.decision_seq);
        setPatched((p) => (r.hold ? { ...p, [ref]: r.hold } : p));
        setNotes((n) => ({ ...n, [ref]: { note, action } }));
        toast.success(action === "confirm_block" ? "Mule confirmed" : "Hold released", "Resolved. Other banks learn from a confirmed mule via the antibody.");
        setAnnounce(action === "confirm_block" ? "Mule confirmed, transfer blocked." : "Hold released.");
      } catch (e) {
        const f = explainResolveError(e, action);
        toast.error(f.title, f.detail);
        setAnnounce(`${f.title}. ${f.detail}`);
        if (f.refresh) rest.reload();
      } finally {
        setPending((p) => {
          const { [ref]: _drop, ...restP } = p;
          void _drop;
          return restP;
        });
        rest.reload();
      }
    },
    [dialog, server, rest],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (dialog || helpOpen) return;
      if (e.key === "j" || e.key === "ArrowDown") {
        if (e.key === "ArrowDown" && tag === "BUTTON") return;
        e.preventDefault();
        move(1);
      } else if (e.key === "k" || e.key === "ArrowUp") {
        if (e.key === "ArrowUp" && tag === "BUTTON") return;
        e.preventDefault();
        move(-1);
      } else if (e.key === "c") openAction("confirm_block");
      else if (e.key === "r") openAction("release");
      else if (e.key === "?") setHelpOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dialog, helpOpen, move, openAction]);

  const dialogHold = dialog ? (views.find((h) => h.txn_ref === dialog.ref) ?? null) : null;
  const noRun = rest.error !== null && rest.data === null;

  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-3 px-4 py-4 md:px-6">
      <div aria-live="polite" role="status" className="sr-only" data-testid="analyst-live">
        {announce}
      </div>
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Analyst Console</h1>
          <p className="text-sm text-muted">Holds sorted by soonest deadline. Overdue holds stay open until a person decides.</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="mono tnum rounded-lg border border-line bg-chip px-2 py-1 text-xs" data-testid="open-count">
            {openCount} open{overdueCount > 0 ? ` · ${overdueCount} overdue` : ""}
          </span>
          <Button variant="ghost" size="sm" onClick={() => setHelpOpen(true)} aria-label="Keyboard shortcuts">
            <Keyboard className="h-4 w-4" aria-hidden /> Shortcuts
          </Button>
          <Button variant="ghost" size="sm" onClick={rest.reload} aria-label="Refresh holds">
            <RefreshCw className={cn("h-4 w-4", rest.loading && "animate-spin")} aria-hidden /> Refresh
          </Button>
        </div>
      </header>

      {!canAct && (
        <p className="flex items-center gap-2 rounded-xl border border-warn/40 bg-warn-soft px-3 py-2 text-sm" data-testid="readonly-banner" role="note">
          <ShieldCheck className="h-4 w-4 shrink-0 text-warn" aria-hidden />
          <span>
            <strong>Read-only.</strong> The {role ?? "current"} role can inspect holds but not resolve them. Switch to Analyst or Admin (top right) to confirm or release.
          </span>
        </p>
      )}

      <div className="grid gap-3 lg:grid-cols-[minmax(340px,440px)_1fr]">
        <Panel
          title="Hold queue"
          icon={<Inbox className="h-3.5 w-3.5" aria-hidden />}
          className="min-w-0"
          actions={
            <div role="group" aria-label="Filter holds" className="flex gap-1">
              {FILTERS.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  aria-pressed={filter === f.key}
                  onClick={() => setFilter(f.key)}
                  className={cn(
                    "rounded-md px-1.5 py-0.5 text-[10.5px] font-medium",
                    filter === f.key ? "bg-raised text-fg" : "text-muted hover:text-fg",
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>
          }
        >
          {rest.loading && rest.data === null ? (
            <div className="space-y-2 p-3" aria-busy="true" aria-label="Loading holds">
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          ) : noRun ? (
            <EmptyState
              icon={<Inbox className="h-5 w-5" aria-hidden />}
              title={rest.forbidden ? "This role cannot see holds" : "No run yet"}
              hint={rest.forbidden ? "Switch to Analyst, Officer or Admin." : "Start the live scenario from the Live page; holds appear here as they are created."}
            >
              <Button size="sm" variant="secondary" onClick={rest.reload}>
                Try again
              </Button>
            </EmptyState>
          ) : shown.length === 0 ? (
            <EmptyState
              icon={<Inbox className="h-5 w-5" aria-hidden />}
              title={filter === "resolved" ? "No resolved holds" : "No open holds"}
              hint={filter === "open" ? "New holds appear here live." : undefined}
            />
          ) : (
            <div
              ref={listRef}
              className="overflow-y-auto"
              style={{ height: Math.min(VIEW_H, shown.length * ROW_HEIGHT + 8) }}
              onScroll={v.onScroll}
              tabIndex={0}
              aria-label="Hold list (J and K move the selection)"
              data-testid="hold-list"
            >
              <ul style={{ paddingTop: v.padTop, paddingBottom: v.padBottom }}>
                {shown.slice(v.start, v.end).map((h) => (
                  <HoldRow
                    key={h.txn_ref}
                    hold={h}
                    simT={simT}
                    selected={h.txn_ref === selected}
                    pendingLabel={pending[h.txn_ref] ? "sending…" : undefined}
                    banks={run.banks}
                    onSelect={setSelected}
                  />
                ))}
              </ul>
            </div>
          )}
        </Panel>

        <Panel title="Selected hold" className="min-w-0" bodyClassName="overflow-y-auto">
          {current ? (
            <HoldDetail
              hold={current}
              run={run}
              simT={simT}
              canAct={canAct}
              pending={pending[current.txn_ref]?.action}
              note={notes[current.txn_ref]}
              onAction={openAction}
            />
          ) : (
            <EmptyState title="No open holds" hint="Select a hold to see its reasons, history and timeline." />
          )}
        </Panel>
      </div>

      <ResolveDialog hold={dialogHold} action={dialog?.action ?? null} onCancel={() => setDialog(null)} onConfirm={(n) => void resolve(n)} />

      <Dialog open={helpOpen} onOpenChange={setHelpOpen}>
        <DialogContent className="w-[min(94vw,420px)]">
          <DialogTitle className="text-lg font-semibold">Keyboard shortcuts</DialogTitle>
          <DialogDescription className="text-sm text-muted">Shortcuts are ignored while you type in a field.</DialogDescription>
          <dl className="mt-4 space-y-2">
            {SHORTCUTS.map(([k, d]) => (
              <div key={k} className="flex items-center justify-between gap-4 text-sm">
                <dt>
                  <kbd className="mono rounded border border-line-strong bg-chip px-1.5 py-0.5 text-xs">{k}</kbd>
                </dt>
                <dd className="text-muted">{d}</dd>
              </div>
            ))}
          </dl>
        </DialogContent>
      </Dialog>
    </div>
  );
}
