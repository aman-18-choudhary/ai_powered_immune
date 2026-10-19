import {
  BookOpenCheck,
  FilePlus2,
  FileText,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  ListChecks,
  LogIn,
  MapPinned,
  BrainCircuit,
  ScanSearch,
  Radar,
  ShieldCheck,
  Share2,
  Smartphone,
  UserCog,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import type { AccountRole } from "@/lib/complaints/types";

export interface NavItem {
  to: string;
  label: string;
  title: string;
  icon: LucideIcon;
  end?: boolean;
  /** Not highlighted under these sub-paths (they have their own entries). */
  inactiveUnder?: readonly string[];
}

/** Simulator pages (need a demo role; everything on them is simulated). */
export const NAV: readonly NavItem[] = [
  { to: "/", label: "Live", title: "Live Interception", icon: Radar, end: true },
  { to: "/command", label: "Command", title: "Command Centre", icon: LayoutDashboard },
  { to: "/analyst", label: "Analyst", title: "Analyst Console", icon: ShieldCheck },
  { to: "/evidence", label: "Evidence", title: "Evidence Ledger", icon: BookOpenCheck },
  { to: "/citizen", label: "Citizen", title: "Citizen Shield", icon: Smartphone },
  { to: "/insights", label: "Insights", title: "Insights", icon: Gauge },
  { to: "/about", label: "About", title: "About", icon: FlaskConical },
];

export interface AccountNavItem extends NavItem {
  roles: readonly AccountRole[];
}

/** Account pages (real complaints service), shown by the EFFECTIVE role from /auth/session. */
export const ACCOUNT_NAV: readonly AccountNavItem[] = [
  { to: "/me", label: "Reports", title: "My complaints", icon: FileText, end: true, roles: ["CITIZEN"] },
  { to: "/me/complaints/new", label: "Report", title: "Register a complaint", icon: FilePlus2, roles: ["CITIZEN"] },
  { to: "/check", label: "Check", title: "Check a message or link", icon: ScanSearch, roles: ["CITIZEN", "INVESTIGATOR", "ADMIN"] },
  { to: "/investigator", label: "Queue", title: "Investigator queue", icon: ListChecks, inactiveUnder: ["/investigator/network", "/investigator/map"], roles: ["INVESTIGATOR", "ADMIN"] },
  { to: "/investigator/network", label: "Network", title: "Complaint network", icon: Share2, roles: ["INVESTIGATOR", "ADMIN"] },
  { to: "/investigator/map", label: "Map", title: "Complaint map", icon: MapPinned, roles: ["INVESTIGATOR", "ADMIN"] },
  { to: "/admin", label: "Admin", title: "Administration", icon: UserCog, roles: ["ADMIN"] },
  { to: "/models", label: "Models", title: "Models", icon: BrainCircuit, roles: ["CITIZEN", "INVESTIGATOR", "ADMIN"] },
  { to: "/me/profile", label: "Profile", title: "Profile and privacy", icon: UserRound, roles: ["CITIZEN", "INVESTIGATOR", "ADMIN"] },
];

export const SIGN_IN_NAV: NavItem = { to: "/sign-in", label: "Sign in", title: "Sign in", icon: LogIn };
export const SIMULATION_NAV: NavItem = { to: "/simulation", label: "Demo", title: "Simulation demo", icon: FlaskConical };

/** Items for the rail and the palette: simulator pages when a demo role is active, account pages by role. */
export function navItems(simSignedIn: boolean, role: AccountRole | null, accountSignedIn: boolean): NavItem[] {
  const out: NavItem[] = simSignedIn ? [...NAV] : [];
  if (role) out.push(...ACCOUNT_NAV.filter((n) => n.roles.includes(role)));
  else if (!accountSignedIn) out.push(SIGN_IN_NAV);
  if (!simSignedIn) out.push(SIMULATION_NAV);
  return out;
}

/** Ordered from general to specific: the last match wins. */
const TITLES: { to: string; title: string; end?: boolean }[] = [
  ...NAV,
  SIGN_IN_NAV,
  SIMULATION_NAV,
  { to: "/account", title: "Account" },
  { to: "/me", title: "My complaints" },
  { to: "/me/complaints/", title: "Complaint details" },
  { to: "/me/complaints/new", title: "Register a complaint", end: true },
  { to: "/me/profile", title: "Profile and privacy" },
  { to: "/investigator", title: "Investigator queue" },
  { to: "/investigator/complaints/", title: "Complaint review" },
  { to: "/investigator/network", title: "Complaint network" },
  { to: "/investigator/map", title: "Complaint map" },
  { to: "/admin", title: "Administration" },
  { to: "/check", title: "Check a message or link" },
  { to: "/models", title: "Models" },
];

export function pageTitle(path: string): string {
  const hit = [...TITLES].reverse().find((n) => (n.end ? path === n.to : path === n.to || path.startsWith(n.to.endsWith("/") ? n.to : `${n.to}/`)));
  return hit?.title ?? "Not found";
}

const ACCOUNT_PREFIXES = ["/me", "/investigator", "/admin", "/sign-in", "/account", "/check", "/models"];
/** Pages that show real records (no SIMULATION pill there). */
export function isAccountPath(path: string): boolean {
  return ACCOUNT_PREFIXES.some((p) => path === p || path.startsWith(`${p}/`));
}
