import { Suspense, useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { Activity, LogOut, Moon, Search, Sun, Waves } from "lucide-react";
import { BrandMark, BrandWord } from "@/components/common/Brand";
import { Skeleton } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Tooltip } from "@/components/ui/tooltip";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useTheme } from "@/lib/theme";
import { useMotionPrefs } from "@/lib/motion/prefs";
import { routeVariants } from "@/lib/motion/variants";
import { useSession } from "@/state/session";
import { SimulationPill } from "./SimulationPill";
import { ConnectionStatus } from "./ConnectionStatus";
import { CommandPalette, openPalette, paletteShortcut } from "./CommandPalette";
import { NAV, pageTitle } from "./nav";
import { cn } from "@/lib/utils/cn";

function Rail() {
  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-40 flex items-center gap-1 overflow-x-auto border-t border-line bg-solid/95 px-2 py-1.5 backdrop-blur md:inset-y-0 md:left-0 md:right-auto md:w-[84px] md:flex-col md:justify-start md:gap-1.5 md:overflow-visible md:border-r md:border-t-0 md:px-2 md:py-4"
    >
      <NavLink to="/" aria-label="ScamShield Immune home" className="mb-3 hidden md:block">
        <BrandMark size={36} />
      </NavLink>
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            cn(
              "group relative flex min-w-[58px] flex-col items-center gap-1 rounded-xl px-2 py-2 text-[10.5px] font-medium transition-colors md:w-full",
              isActive ? "bg-intel-soft text-fg" : "text-muted hover:bg-hover hover:text-fg",
            )
          }
        >
          {({ isActive }) => (
            <>
              {isActive && (
                <span className="absolute left-0 top-2 hidden h-[calc(100%-16px)] w-[3px] rounded-r bg-intel md:block" aria-hidden />
              )}
              <Icon className={cn("h-[18px] w-[18px]", isActive && "text-intel")} aria-hidden />
              {label}
            </>
          )}
        </NavLink>
      ))}
    </nav>
  );
}

function TopBar() {
  const { pathname } = useLocation();
  const { theme, toggle } = useTheme();
  const { reduced, setReduced } = useMotionPrefs();
  const role = useSession((s) => s.role);
  const logout = useSession((s) => s.logout);
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-[color-mix(in_srgb,var(--bg)_82%,transparent)] px-4 backdrop-blur-md md:px-6">
      <div className="md:hidden">
        <BrandMark size={26} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="hidden md:block">
          <BrandWord />
        </div>
        <div className="truncate text-sm font-semibold md:hidden">{pageTitle(pathname)}</div>
      </div>
      <div className="hidden items-center gap-2 text-xs text-muted lg:flex">
        <Activity className="h-3.5 w-3.5" aria-hidden />
        <span className="font-medium text-fg">{pageTitle(pathname)}</span>
      </div>
      <SimulationPill />
      <Button variant="ghost" size="sm" onClick={openPalette} aria-label="Open command palette" className="hidden gap-1.5 sm:inline-flex">
        <Search className="h-4 w-4" aria-hidden />
        <kbd className="mono rounded border border-line px-1 text-[10px] text-subtle">{paletteShortcut()}</kbd>
      </Button>
      <div className="hidden sm:block">
        <ConnectionStatus />
      </div>
      <Popover>
        <PopoverTrigger asChild>
          <Button variant="ghost" size="sm" aria-label="Preferences and account" className="gap-1.5">
            <Waves className="h-4 w-4" aria-hidden />
            <span className="hidden capitalize sm:inline">{role}</span>
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-64 space-y-3">
          <div className="flex items-center justify-between text-sm">
            <span>Signed in as</span>
            <span className="rounded-md border border-line bg-chip px-2 py-0.5 text-xs font-medium capitalize">{role}</span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <label htmlFor="reduce-motion">Reduce motion</label>
            <Switch id="reduce-motion" checked={reduced} onCheckedChange={setReduced} aria-label="Reduce motion" />
          </div>
          <div className="flex items-center justify-between text-sm sm:hidden">
            <span>Stream</span>
            <ConnectionStatus />
          </div>
          <Button variant="secondary" size="sm" className="w-full" onClick={() => logout()}>
            <LogOut className="h-4 w-4" aria-hidden /> Switch role
          </Button>
        </PopoverContent>
      </Popover>
      <Tooltip content={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}>
        <Button variant="ghost" size="icon" onClick={toggle} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}>
          {theme === "dark" ? <Sun className="h-4 w-4" aria-hidden /> : <Moon className="h-4 w-4" aria-hidden />}
        </Button>
      </Tooltip>
    </header>
  );
}

function RouteFallback() {
  return (
    <div className="space-y-4 p-6" aria-busy="true" aria-label="Loading page">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

/** Moves focus to <main> and announces the page after a client-side navigation (not on first load). */
function useRouteFocus(pathname: string) {
  const first = useRef(true);
  const [announce, setAnnounce] = useState("");
  useEffect(() => {
    const title = pageTitle(pathname);
    document.title = `${title} · ScamShield Immune`;
    if (first.current) {
      first.current = false;
      return;
    }
    setAnnounce(`${title} page`);
    const h = setTimeout(() => document.getElementById("main")?.focus({ preventScroll: true }), 60);
    return () => clearTimeout(h);
  }, [pathname]);
  return announce;
}

export function Shell() {
  const { pathname } = useLocation();
  const announce = useRouteFocus(pathname);
  return (
    <div className="relative min-h-dvh">
      <div className="backdrop" aria-hidden>
        <div className="aurora a" />
        <div className="aurora b" />
      </div>
      <a
        href="#main"
        className="fixed left-3 top-3 z-[100] -translate-y-20 rounded-lg bg-solid px-3 py-2 text-sm font-medium shadow-lg transition-transform focus:translate-y-0"
      >
        Skip to content
      </a>
      <Rail />
      <CommandPalette />
      <div aria-live="polite" role="status" className="sr-only" data-testid="route-announce">
        {announce}
      </div>
      <div className="relative z-10 min-w-0 pb-[68px] md:pb-0 md:pl-[84px]">
        <TopBar />
        <main id="main" tabIndex={-1} className="outline-none">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={pathname} variants={routeVariants} initial="initial" animate="enter" exit="exit">
              <Suspense fallback={<RouteFallback />}>
                <Outlet />
              </Suspense>
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}
