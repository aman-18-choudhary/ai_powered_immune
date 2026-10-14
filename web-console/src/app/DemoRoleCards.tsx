import { useState } from "react";
import { motion } from "framer-motion";
import { BarChart3, Loader2, ShieldCheck, Smartphone, UserCog, type LucideIcon } from "lucide-react";
import { endpoints } from "@/lib/api/endpoints";
import { friendlyError } from "@/lib/api/client";
import { useSession } from "@/state/session";
import { listContainer, listItem } from "@/lib/motion/variants";
import type { Role } from "@/types/api";

const ROLE_CARDS: { role: Role; title: string; blurb: string; Icon: LucideIcon }[] = [
  { role: "officer", title: "Officer", blurb: "Run the live scenario, watch the command centre, export evidence.", Icon: ShieldCheck },
  { role: "analyst", title: "Analyst", blurb: "Work the hold queue: confirm mules, release false alarms.", Icon: BarChart3 },
  { role: "citizen", title: "Citizen", blurb: "Check a suspicious message or call summary in plain language.", Icon: Smartphone },
  { role: "admin", title: "Admin", blurb: "Everything: control the run, resolve holds, inspect the ledger.", Icon: UserCog },
];

/** The simulator's demo roles (no password, no real data). Separate from account sign-in. */
export function DemoRoleCards({ columns = 2 }: { columns?: 1 | 2 }) {
  const login = useSession((s) => s.login);
  const [busy, setBusy] = useState<Role | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pick = async (role: Role) => {
    setBusy(role);
    setError(null);
    try {
      const r = await endpoints.login(role);
      login({ token: r.token, role: r.role, sub: r.sub });
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <motion.ul variants={listContainer} initial="hidden" animate="show" className={columns === 2 ? "grid gap-3 sm:grid-cols-2" : "grid gap-3"}>
        {ROLE_CARDS.map(({ role, title, blurb, Icon }) => (
          <motion.li key={role} variants={listItem}>
            <button
              type="button"
              data-testid={`role-${role}`}
              disabled={busy !== null}
              onClick={() => pick(role)}
              className="panel panel-blur group flex w-full items-start gap-4 p-5 text-left transition-[transform,border-color] hover:-translate-y-0.5 hover:border-[var(--border-strong)] disabled:opacity-60"
            >
              <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-line bg-intel-soft text-intel">
                {busy === role ? <Loader2 className="h-5 w-5 animate-spin" aria-hidden /> : <Icon className="h-5 w-5" aria-hidden />}
              </span>
              <span>
                <span className="block text-base font-semibold">{title}</span>
                <span className="mt-0.5 block text-sm text-muted">{blurb}</span>
              </span>
            </button>
          </motion.li>
        ))}
      </motion.ul>
      {error && (
        <p role="alert" className="mt-4 text-center text-sm text-danger-text">
          {error}
        </p>
      )}
    </>
  );
}
