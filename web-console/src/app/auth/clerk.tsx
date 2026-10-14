/**
 * Clerk integration. This module is loaded lazily (its own chunk) and only when GET
 * /api/complaints/auth/config reports `clerk.enabled` with a publishable key. The publishable key
 * is the only Clerk value the console ever sees; no secret key exists in the browser.
 */
import { useEffect, type ReactNode } from "react";
import { ClerkProvider, SignIn, useAuth } from "@clerk/clerk-react";
import { registerClerk, useAccount } from "@/state/account";

function Bridge() {
  const { isLoaded, isSignedIn, getToken, signOut } = useAuth();
  useEffect(() => {
    registerClerk({ getToken: () => getToken(), signOut: () => signOut() });
    return () => registerClerk(null);
  }, [getToken, signOut]);
  useEffect(() => {
    if (!isLoaded) return;
    const acct = useAccount.getState();
    if (isSignedIn) void acct.completeClerkSignIn();
    else if (acct.method === "clerk") acct.signOut();
  }, [isLoaded, isSignedIn]);
  return null;
}

export function ClerkRoot({ publishableKey, children }: { publishableKey: string; children: ReactNode }) {
  return (
    <ClerkProvider publishableKey={publishableKey} afterSignOutUrl="/">
      <Bridge />
      {children}
    </ClerkProvider>
  );
}

export function ClerkSignInPanel() {
  return (
    <div className="flex justify-center" data-testid="clerk-sign-in">
      <SignIn routing="virtual" />
    </div>
  );
}
