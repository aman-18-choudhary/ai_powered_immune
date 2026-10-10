import { BookOpenCheck, FlaskConical, Gauge, LayoutDashboard, Radar, ShieldCheck, Smartphone, type LucideIcon } from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  title: string;
  icon: LucideIcon;
  end?: boolean;
}
export const NAV: readonly NavItem[] = [
  { to: "/", label: "Live", title: "Live Interception", icon: Radar, end: true },
  { to: "/command", label: "Command", title: "Command Centre", icon: LayoutDashboard },
  { to: "/analyst", label: "Analyst", title: "Analyst Console", icon: ShieldCheck },
  { to: "/evidence", label: "Evidence", title: "Evidence Ledger", icon: BookOpenCheck },
  { to: "/citizen", label: "Citizen", title: "Citizen Shield", icon: Smartphone },
  { to: "/insights", label: "Insights", title: "Insights", icon: Gauge },
  { to: "/about", label: "About", title: "About", icon: FlaskConical },
];

export function pageTitle(path: string): string {
  const hit = [...NAV].reverse().find((n) => (n.end ? path === n.to : path.startsWith(n.to)));
  return hit?.title ?? "Not found";
}

