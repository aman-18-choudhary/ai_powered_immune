import { Suspense, useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation, useOutlet } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { Activity, BadgeCheck, LogIn, LogOut, Moon, Search, Sun, Waves, X } from "lucide-react";
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
import { homeFor, useAccount, useAccountRole } from "@/state/account";
import { ROLE_LABEL } from "@/lib/complaints/format";
import { SimulationPill } from "./SimulationPill";
import { ConnectionStatus } from "./ConnectionStatus";
import { CommandPalette, openPalette, paletteShortcut } from "./CommandPalette";
import { isAccountPath, navItems, pageTitle } from "./nav";
import { cn } from "@/lib/utils/cn";

function useNavItems() {
  const simSignedIn = useSession((s) => s.token !== null);
  const role = useAccountRole();
  const accountSignedIn = useAccount((s) => s.status !== "none");
  return navItems(simSignedIn, role, accountSignedIn);
}

function Rail() {
  const items = useNavItems();
  const { pathname } = useLocation();
  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-40 flex items-center gap-1 overflow-x-auto border-t border-line bg-solid/95 px-2 py-1.5 backdrop-blur md:inset-y-0 md:left-0 md:right-auto md:w-[84px] md:flex-col md:justify-start md:gap-1.5 md:overflow-visible md:border-r md:border-t-0 md:px-2 md:py-4"
    >
      <NavLink to="/" aria-label="ScamShield Immune home" className="mb-3 hidden md:block">
        <BrandMark size={36} />
      </NavLink>
      {items.map(({ to, label, icon: Icon, end, inactiveUnder }) => (
        <NavLink
          key={to}
          to={to}
          end={end || (inactiveUnder !== undefined && (pathname === inactiveUnder || pathname.startsWith(`${inactiveUnder}/`)))}
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
  const acctRole = useAccountRole();
  const acctEmail = useAccount((s) => s.session?.user.email ?? null);
  const acctStatus = useAccount((s) => s.status);
  const signOut = useAccount((s) => s.signOut);
  const enterInvestigator = useAccount((s) => s.enterInvestigator);
  const onAccountPage = isAccountPath(pathname);
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
      {!onAccountPage && role && <SimulationPill />}
      <Button variant="ghost" size="sm" onClick={openPalette} aria-label="Open command palette" className="hidden gap-1.5 sm:inline-flex">
        <Search className="h-4 w-4" aria-hidden />
        <kbd className="mono rounded border border-line px-1 text-[10px] text-subtle">{paletteShortcut()}</kbd>
      </Button>
      {role && (
        <div className="hidden sm:block">
          <ConnectionStatus />
        </div>
      )}
      <Popover>
        <PopoverTrigger asChild>
          <Button variant="ghost" size="sm" aria-label="Preferences and account" className="gap-1.5">
            <Waves className="h-4 w-4" aria-hidden />
            <span className="hidden capitalize sm:inline">{acctRole ? ROLE_LABEL[acctRole] : role}</span>
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-72 space-y-3">
          <section aria-label="Account" className="space-y-2 border-b border-line pb-3">
            {acctStatus === "none" ? (
              <Link
                to="/sign-in"
                className="inline-flex h-8 w-full items-center justify-center gap-2 rounded-xl border border-line-strong bg-chip px-3 text-xs font-medium hover:bg-hover"
              >
                <LogIn className="h-4 w-4" aria-hidden /> Sign in to your account
              </Link>
            ) : (
              <>
                <div className="flex items-center justify-between gap-2 text-sm">
                  <span className="min-w-0 truncate" title={acctEmail ?? undefined} data-testid="account-email">
                    {acctEmail ?? "Account"}
                  </span>
                  <span className="shrink-0 rounded-md border border-line bg-chip px-2 py-0.5 text-xs font-medium" data-testid="account-role">
                    {acctRole ? ROLE_LABEL[acctRole] : "…"}
                  </span>
                </div>
                {acctRole === "CITIZEN" && (
                  <Button variant="ghost" size="sm" className="w-full" onClick={() => void enterInvestigator()}>
                    <BadgeCheck className="h-4 w-4" aria-hidden /> Enter as investigator
                  </Button>
                )}
                <Button variant="secondary" size="sm" className="w-full" onClick={() => signOut()} data-testid="account-sign-out">
                  <LogOut className="h-4 w-4" aria-hidden /> Sign out of account
                </Button>
              </>
            )}
          </section>
          {role ? (
            <div className="flex items-center justify-between text-sm">
              <span>Simulation demo role</span>
              <span className="rounded-md border border-line bg-chip px-2 py-0.5 text-xs font-medium capitalize">{role}</span>
            </div>
          ) : (
            <Link to="/simulation" className="block text-sm text-flow underline underline-offset-2">
              Open the simulation demo
            </Link>
          )}
          <div className="flex items-center justify-between text-sm">
            <label htmlFor="reduce-motion">Reduce motion</label>
            <Switch id="reduce-motion" checked={reduced} onCheckedChange={setReduced} aria-label="Reduce motion" />
          </div>
          {role && (
            <div className="flex items-center justify-between text-sm sm:hidden">
              <span>Stream</span>
              <ConnectionStatus />
            </div>
          )}
          {role && (
            <Button variant="secondary" size="sm" className="w-full" onClick={() => logout()}>
              <LogOut className="h-4 w-4" aria-hidden /> Switch role
            </Button>
          )}
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

/** Lapsed investigator/admin access (from /auth/session) and a denied investigator entry. */
function AccountBanner() {
  const session = useAccount((s) => s.session);
  const denied = useAccount((s) => s.entryDenied);
  const dismiss = useAccount((s) => s.dismissEntryDenied);
  const lapsed = session && (session.investigator_access === "lapsed" || session.admin_access === "lapsed" || session.admin_access === "lapsed_retained") ? session.message : null;
  if (!lapsed && !denied) return null;
  return (
    <div className="space-y-2 px-4 pt-3 md:px-6" data-testid="account-banner">
      {denied && (
        <div role="alert" className="flex items-start justify-between gap-3 rounded-xl border border-warn/40 bg-warn-soft p-3 text-sm" data-testid="investigator-denied">
          <p>
            <strong>Investigator access not approved.</strong> {denied}{" "}
            <Link to={homeFor(session?.user.role ?? null)} className="font-medium text-flow underline underline-offset-2">
              Go to your dashboard
            </Link>
          </p>
          <button type="button" onClick={dismiss} aria-label="Dismiss" className="rounded p-1 text-muted hover:text-fg">
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
      )}
      {lapsed && (
        <p role="status" className="rounded-xl border border-warn/40 bg-warn-soft p-3 text-sm" data-testid="access-lapsed">
          {lapsed}
        </p>
      )}
    </div>
  );
}

/**
 * The route element captured when this page mounted. Without it the page that is animating out
 * would already render the NEW route (an Outlet reads the current location), which then remounts
 * when the exit finishes and loses anything typed during the transition.
 */
function FrozenOutlet() {
  const outlet = useOutlet();
  const [frozen] = useState(outlet);
  return frozen;
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
        <AccountBanner />
        <main id="main" tabIndex={-1} className="outline-none">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={pathname} variants={routeVariants} initial="initial" animate="enter" exit="exit">
              <Suspense fallback={<RouteFallback />}>
                <FrozenOutlet />
              </Suspense>
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}
