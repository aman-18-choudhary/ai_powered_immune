import { create } from "zustand";
import { complaintsApi, configureComplaintsAuth, ComplaintsApiError, messageFor } from "@/lib/complaints/api";
import type { AccountRole, AuthConfig, SessionPayload } from "@/lib/complaints/types";
import { isRecord } from "@/types/guards";
import { jwtExpiry } from "./session";

/**
 * The signed-in ACCOUNT for the real complaints service (separate from the simulator's demo role
 * in `session.ts`). The role is never stored or sent by the console: it is read from
 * GET /auth/session (the service decides it from its users table) on every sign-in and reload.
 */

const KEY = "ssi.account";
const ENTRY_KEY = "ssi.account.entry";

export type SignInMethod = "local" | "clerk";
export type Entry = "citizen" | "investigator";

interface ClerkHooks {
  getToken: () => Promise<string | null>;
  signOut: () => Promise<void>;
}
let clerk: ClerkHooks | null = null;

/** Called by the lazily loaded Clerk bridge (only when /auth/config enables Clerk). */
export function registerClerk(h: ClerkHooks | null) {
  clerk = h;
}

function loadLocal(): string | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const p: unknown = JSON.parse(raw);
    if (!isRecord(p) || p.method !== "local" || typeof p.token !== "string") return null;
    const exp = jwtExpiry(p.token);
    if (exp !== null && exp * 1000 < Date.now() + 5000) return null;
    return p.token;
  } catch {
    return null;
  }
}

function loadEntry(): Entry {
  try {
    return sessionStorage.getItem(ENTRY_KEY) === "investigator" ? "investigator" : "citizen";
  } catch {
    return "citizen";
  }
}

export interface AccountState {
  config: AuthConfig | null;
  configStatus: "idle" | "loading" | "ready" | "unavailable";
  configError: string | null;
  method: SignInMethod | null;
  token: string | null;
  status: "none" | "loading" | "ready" | "error";
  session: SessionPayload | null;
  error: string | null;
  notice: "expired" | null;
  /** The service's friendly 403 message after an investigator entry that was not approved. */
  entryDenied: string | null;
  pendingEntry: Entry;
  /** Set when a sign-in completes so the router can open the role's home page once. */
  justSignedIn: boolean;
  consumeJustSignedIn: () => void;
  setPendingEntry: (e: Entry) => void;
  loadConfig: () => Promise<void>;
  completeLocalSignIn: (token: string, entry: Entry) => Promise<void>;
  completeClerkSignIn: () => Promise<void>;
  refresh: () => Promise<void>;
  enterInvestigator: () => Promise<boolean>;
  dismissEntryDenied: () => void;
  signOut: (reason?: "expired") => void;
}

const initialToken = loadLocal();

export const useAccount = create<AccountState>((set, get) => {
  /** Session after sign-in, then the investigator entry when it was chosen. */
  const establish = async (entry: Entry, token?: string) => {
    let session = await complaintsApi.session(token);
    let denied: string | null = null;
    if (entry === "investigator") {
      try {
        session = await complaintsApi.entry("investigator", token);
      } catch (e) {
        if (e instanceof ComplaintsApiError && e.status === 403) denied = e.message;
        else throw e;
      }
    }
    try {
      sessionStorage.removeItem(ENTRY_KEY);
    } catch {
      /* ignore */
    }
    set({ session, status: "ready", error: null, entryDenied: denied, notice: null, justSignedIn: true });
  };

  return {
    config: null,
    configStatus: "idle",
    configError: null,
    method: initialToken ? "local" : null,
    token: initialToken,
    status: initialToken ? "loading" : "none",
    session: null,
    error: null,
    notice: null,
    entryDenied: null,
    pendingEntry: loadEntry(),
    justSignedIn: false,
    consumeJustSignedIn: () => set({ justSignedIn: false }),

    setPendingEntry: (e) => {
      try {
        sessionStorage.setItem(ENTRY_KEY, e);
      } catch {
        /* ignore */
      }
      set({ pendingEntry: e });
    },

    loadConfig: async () => {
      if (get().configStatus === "loading") return;
      set({ configStatus: "loading", configError: null });
      const ctl = new AbortController();
      const h = setTimeout(() => ctl.abort(), 8000);
      try {
        const config = await complaintsApi.authConfig(ctl.signal);
        set({ config, configStatus: "ready" });
      } catch (e) {
        set({ configStatus: "unavailable", configError: ctl.signal.aborted ? "The complaints service did not answer in time." : messageFor(e) });
      } finally {
        clearTimeout(h);
      }
    },

    completeLocalSignIn: async (token, entry) => {
      set({ method: "local", token, status: "loading", error: null, entryDenied: null });
      try {
        await establish(entry, token);
        try {
          sessionStorage.setItem(KEY, JSON.stringify({ method: "local", token }));
        } catch {
          /* private mode: memory only */
        }
      } catch (e) {
        set({ method: null, token: null, status: "none", session: null });
        throw e;
      }
    },

    completeClerkSignIn: async () => {
      if (get().status === "ready" && get().method === "clerk") return;
      set({ method: "clerk", token: null, status: "loading", error: null, entryDenied: null });
      try {
        await establish(get().pendingEntry);
      } catch (e) {
        set({ status: "error", error: messageFor(e) });
      }
    },

    refresh: async () => {
      if (!get().method) return;
      set({ status: get().session ? get().status : "loading" });
      try {
        const session = await complaintsApi.session();
        set({ session, status: "ready", error: null });
      } catch (e) {
        if (e instanceof ComplaintsApiError && e.status === 401) return; // signOut("expired") already ran
        set({ status: "error", error: messageFor(e) });
      }
    },

    enterInvestigator: async () => {
      try {
        const session = await complaintsApi.entry("investigator");
        set({ session, entryDenied: null });
        return true;
      } catch (e) {
        set({ entryDenied: messageFor(e) });
        return false;
      }
    },

    dismissEntryDenied: () => set({ entryDenied: null }),

    signOut: (reason) => {
      const wasClerk = get().method === "clerk";
      try {
        sessionStorage.removeItem(KEY);
        sessionStorage.removeItem(ENTRY_KEY);
      } catch {
        /* ignore */
      }
      set({ method: null, token: null, status: "none", session: null, error: null, entryDenied: null, notice: reason ?? null });
      if (wasClerk && clerk) void clerk.signOut().catch(() => {});
    },
  };
});

configureComplaintsAuth({
  getToken: async () => {
    const s = useAccount.getState();
    if (s.method === "local") return s.token;
    if (s.method === "clerk" && clerk) {
      try {
        return await clerk.getToken();
      } catch {
        return null;
      }
    }
    return null;
  },
  onUnauthorized: () => useAccount.getState().signOut("expired"),
});

/** The effective role from the last /auth/session answer (null when signed out). */
export function useAccountRole(): AccountRole | null {
  return useAccount((s) => (s.status === "ready" || s.status === "error" ? (s.session?.user.role ?? null) : null));
}

export function homeFor(role: AccountRole | null): string {
  if (role === "ADMIN") return "/admin";
  if (role === "INVESTIGATOR") return "/investigator";
  return "/me";
}

export const isStaff = (r: AccountRole | null) => r === "INVESTIGATOR" || r === "ADMIN";
