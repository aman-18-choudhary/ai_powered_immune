import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Command as CommandIcon, Pause, Play, Search, SunMoon, Waves } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { navItems } from "./nav";
import { useAccount, useAccountRole } from "@/state/account";
import { filterPalette, nextIndex, type PaletteItem } from "@/lib/palette";
import { useTheme } from "@/lib/theme";
import { useMotionPrefs } from "@/lib/motion/prefs";
import { useRunStore } from "@/state/runStore";
import { useSession, canControl } from "@/state/session";
import { playback } from "@/state/playback";
import { useEvidence } from "@/state/evidence";
import { cn } from "@/lib/utils/cn";

export const OPEN_PALETTE_EVENT = "ssi:open-palette";

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const nav = useNavigate();
  const id = useId();
  const { toggle: toggleTheme, theme } = useTheme();
  const { reduced, setReduced } = useMotionPrefs();
  const role = useSession((s) => s.role);
  const acctRole = useAccountRole();
  const acctSignedIn = useAccount((s) => s.status !== "none");
  const status = useRunStore((s) => s.run.status);
  const control = canControl(role);
  const running = status === "running" || status === "starting" || status === "seeking";
  const hasRun = status !== "idle";

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener(OPEN_PALETTE_EVENT, onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener(OPEN_PALETTE_EVENT, onOpen);
    };
  }, []);

  useEffect(() => {
    if (open) {
      setQ("");
      setActive(0);
    }
  }, [open]);

  const items = useMemo<PaletteItem[]>(() => {
    const list: PaletteItem[] = navItems(role !== null, acctRole, acctSignedIn).map((n) => ({ id: `go:${n.to}`, label: n.title, hint: n.to, group: "Go to" as const, keywords: n.label }));
    if (control) {
      list.push({ id: "demo:start", label: hasRun ? "Restart the demo" : "Start the demo", hint: "Play the hero scenario", group: "Demo", keywords: "play run scenario" });
      if (hasRun && status !== "finished" && status !== "stopped" && status !== "error")
        list.push({ id: running ? "demo:pause" : "demo:resume", label: running ? "Pause the demo" : "Resume the demo", group: "Demo", keywords: "playback" });
    }
    list.push({ id: "pref:theme", label: theme === "dark" ? "Switch to light theme" : "Switch to dark theme", group: "Preferences", keywords: "dark light appearance" });
    list.push({ id: "pref:motion", label: reduced ? "Turn animations on" : "Reduce motion", group: "Preferences", keywords: "animation accessibility" });
    return list;
  }, [control, hasRun, running, status, theme, reduced, role, acctRole, acctSignedIn]);

  const shown = useMemo(() => filterPalette(items, q), [items, q]);
  useEffect(() => setActive(0), [q]);

  const run = useCallback(
    (item: PaletteItem | undefined) => {
      if (!item) return;
      setOpen(false);
      if (item.id.startsWith("go:")) nav(item.id.slice(3));
      else if (item.id === "demo:start") {
        nav("/");
        useEvidence.getState().reset();
        void playback.start();
      } else if (item.id === "demo:pause") void playback.pause();
      else if (item.id === "demo:resume") void playback.resume();
      else if (item.id === "pref:theme") toggleTheme();
      else if (item.id === "pref:motion") setReduced(!reduced);
    },
    [nav, toggleTheme, setReduced, reduced],
  );

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => nextIndex(a, 1, shown.length));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => nextIndex(a, -1, shown.length));
    } else if (e.key === "Enter") {
      e.preventDefault();
      run(shown[active]);
    }
  };

  const Icon = (i: PaletteItem) =>
    i.group === "Go to" ? CommandIcon : i.id === "demo:pause" ? Pause : i.group === "Demo" ? Play : i.id === "pref:theme" ? SunMoon : Waves;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="top-[18%] w-[min(94vw,560px)] translate-y-0 p-0" aria-describedby={`${id}-d`} data-testid="palette">
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <DialogDescription id={`${id}-d`} className="sr-only">
          Type to jump to a page or run an action. Use the arrow keys and Enter.
        </DialogDescription>
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <Search className="h-4 w-4 text-muted" aria-hidden />
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKeyDown}
            role="combobox"
            aria-expanded
            aria-controls={`${id}-l`}
            aria-activedescendant={shown[active] ? `${id}-o${active}` : undefined}
            aria-label="Search pages and actions"
            placeholder="Jump to a page or run an action…"
            className="w-full bg-transparent text-sm outline-none placeholder:text-subtle"
            data-testid="palette-input"
          />
          <kbd className="mono rounded border border-line px-1.5 text-[10px] text-subtle">Esc</kbd>
        </div>
        <ul id={`${id}-l`} role="listbox" aria-label="Results" className="max-h-[50dvh] overflow-y-auto p-2">
          {shown.length === 0 && <li className="px-3 py-6 text-center text-sm text-muted">Nothing matches &quot;{q}&quot;.</li>}
          {shown.map((it, i) => {
            const I = Icon(it);
            const newGroup = i === 0 || shown[i - 1]?.group !== it.group;
            return (
              <li key={it.id} role="presentation">
                {newGroup && <div className="px-3 pb-1 pt-2 text-[10.5px] font-semibold uppercase tracking-wider text-subtle">{it.group}</div>}
                <div
                  id={`${id}-o${i}`}
                  role="option"
                  aria-selected={i === active}
                  onMouseMove={() => setActive(i)}
                  onClick={() => run(it)}
                  className={cn("flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-sm", i === active ? "bg-intel-soft" : "hover:bg-hover")}
                >
                  <I className="h-4 w-4 text-muted" aria-hidden />
                  <span className="flex-1">{it.label}</span>
                  {it.hint && <span className="mono text-[11px] text-subtle">{it.hint}</span>}
                </div>
              </li>
            );
          })}
        </ul>
      </DialogContent>
    </Dialog>
  );
}

export function openPalette() {
  window.dispatchEvent(new Event(OPEN_PALETTE_EVENT));
}

/** Platform-aware label for the shortcut (⌘K on Apple devices, Ctrl K elsewhere). */
export function paletteShortcut(): string {
  return typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘K" : "Ctrl K";
}
