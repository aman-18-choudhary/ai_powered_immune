import { FlaskConical } from "lucide-react";
import { BrandMark } from "@/components/common/Brand";
import { SimulationPill } from "./SimulationPill";
import { DemoRoleCards } from "./DemoRoleCards";
import { AccountSignIn } from "./auth/AccountSignIn";
import { useSession } from "@/state/session";

export function Splash() {
  const notice = useSession((s) => s.notice);

  return (
    <div className="relative grid min-h-dvh place-items-center px-4 py-10">
      <div className="backdrop" aria-hidden>
        <div className="aurora a" />
        <div className="aurora b" />
      </div>
      <main className="relative z-10 w-full max-w-5xl">
        <div className="mb-8 flex flex-col items-center text-center">
          <BrandMark size={64} />
          <h1 className="mt-5 text-4xl font-semibold tracking-tight sm:text-5xl">
            ScamShield <span className="bg-[linear-gradient(135deg,var(--intel),var(--flow))] bg-clip-text text-transparent">Immune</span>
          </h1>
          <p className="mt-3 text-lg text-muted">Report cyber fraud, and explore how one bank&apos;s detection protects every bank.</p>
        </div>

        <div className="grid items-start gap-6 lg:grid-cols-2">
          <AccountSignIn />

          <section aria-labelledby="sim-h" className="space-y-3 rounded-[var(--panel-radius)] border border-dashed border-warn/40 p-4 sm:p-5" data-testid="simulation-demo">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 id="sim-h" className="flex items-center gap-2 text-lg font-semibold">
                <FlaskConical className="h-5 w-5 text-warn" aria-hidden /> Simulation demo
              </h2>
              <SimulationPill />
            </div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-subtle">Choose a demo role. No password, no real data.</p>
            {notice === "expired" && (
              <p role="status" className="rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-center text-sm text-warn">
                Your demo session expired. Please choose a role to continue.
              </p>
            )}
            <DemoRoleCards />
            <p className="text-center text-xs text-subtle">
              Demo-only sign-in. Tokens stay in this tab&apos;s memory and session storage and are never logged.
            </p>
          </section>
        </div>
      </main>
    </div>
  );
}
