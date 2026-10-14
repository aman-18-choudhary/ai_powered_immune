import { useEffect, type ReactNode } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { FlaskConical, ShieldAlert } from "lucide-react";
import { DemoRoleCards } from "./DemoRoleCards";
import { AccountSignIn } from "./auth/AccountSignIn";
import { SimulationPill } from "./SimulationPill";
import { ErrorBlock, LoadingBlock } from "@/components/complaints/ui";
import { ROLE_LABEL } from "@/lib/complaints/format";
import type { AccountRole } from "@/lib/complaints/types";
import { homeFor, useAccount, useAccountRole } from "@/state/account";
import { useSession } from "@/state/session";
import { usePageTitle } from "@/lib/hooks/usePageTitle";

/** Simulator pages need a demo role; without one, offer the demo role cards in place. */
export function SimOnly({ children }: { children: ReactNode }) {
  const token = useSession((s) => s.token);
  return token ? <>{children}</> : <SimulationGate />;
}

export function SimulationGate() {
  usePageTitle("Simulation demo");
  const token = useSession((s) => s.token);
  return (
    <div className="mx-auto max-w-3xl space-y-4 px-4 py-6 md:px-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <FlaskConical className="h-6 w-6 text-warn" aria-hidden /> Simulation demo
        </h1>
        <SimulationPill />
      </div>
      <p className="text-sm text-muted">
        This page is part of the simulator. Everything there is simulated: calls, victims, banks and mules are synthetic. Choose a demo role. No
        password, no real data.
      </p>
      {token ? (
        <p className="text-sm">
          A demo role is active. <Link to="/" className="text-flow underline-offset-2 hover:underline">Open the live page</Link>.
        </p>
      ) : (
        <DemoRoleCards />
      )}
    </div>
  );
}

export function SignInPage() {
  usePageTitle("Sign in");
  const status = useAccount((s) => s.status);
  const role = useAccountRole();
  if (status === "ready" && role) return <Navigate to={homeFor(role)} replace />;
  return (
    <div className="mx-auto max-w-xl px-4 py-6 md:px-6">
      <AccountSignIn />
    </div>
  );
}

/** Opens the role's home page once, right after a sign-in completes. */
export function PostSignInRedirect() {
  const just = useAccount((s) => s.justSignedIn);
  const consume = useAccount((s) => s.consumeJustSignedIn);
  const role = useAccountRole();
  const nav = useNavigate();
  useEffect(() => {
    if (just && role) {
      consume();
      nav(homeFor(role), { replace: true });
    }
  }, [just, role, consume, nav]);
  return null;
}

/** `/` without a demo role, and `/account`: the effective role's home. */
export function AccountIndex() {
  const status = useAccount((s) => s.status);
  const role = useAccountRole();
  const error = useAccount((s) => s.error);
  const refresh = useAccount((s) => s.refresh);
  if (status === "loading") return <LoadingBlock label="Loading your account" />;
  if (status === "none") return <SignInPage />;
  if (!role) return <ErrorBlock message={error ?? "Your account could not be loaded."} onRetry={() => void refresh()} unavailable />;
  return <Navigate to={homeFor(role)} replace />;
}

/** Account pages: signed in, and (when given) one of `roles` as the effective role. */
export function RequireAccount({ roles, children }: { roles?: readonly AccountRole[]; children: ReactNode }) {
  const status = useAccount((s) => s.status);
  const role = useAccountRole();
  const error = useAccount((s) => s.error);
  const refresh = useAccount((s) => s.refresh);
  if (status === "none") return <SignInPage />;
  if (status === "loading" || (!role && status !== "error")) return <LoadingBlock label="Loading your account" />;
  if (!role) return <ErrorBlock message={error ?? "Your account could not be loaded."} onRetry={() => void refresh()} unavailable />;
  if (roles && !roles.includes(role)) {
    return (
      <div className="mx-auto max-w-xl px-4 py-10 md:px-6" data-testid="role-mismatch">
        <div role="alert" className="panel space-y-2 p-5">
          <h1 className="flex items-center gap-2 text-lg font-semibold">
            <ShieldAlert className="h-5 w-5 text-warn" aria-hidden /> This page is not available for your account
          </h1>
          <p className="text-sm text-muted">
            It is for {roles.map((r) => ROLE_LABEL[r]).join(" or ")} accounts. Your account role is {ROLE_LABEL[role]}.
          </p>
          <Link to={homeFor(role)} className="inline-block text-sm font-medium text-flow underline-offset-2 hover:underline">
            Go to your dashboard
          </Link>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
