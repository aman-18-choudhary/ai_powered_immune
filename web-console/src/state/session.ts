import { create } from "zustand";
import { ROLES, type Role } from "@/types/api";
import { isRecord } from "@/types/guards";

const KEY = "ssi.session";

interface Stored {
  token: string;
  role: Role;
  sub: string;
}

/** Decode the JWT `exp` (seconds) without verifying: only to avoid re-using an expired token. */
export function jwtExpiry(token: string): number | null {
  try {
    const part = token.split(".")[1];
    if (!part) return null;
    const json = atob(part.replace(/-/g, "+").replace(/_/g, "/"));
    const p: unknown = JSON.parse(json);
    return isRecord(p) && typeof p.exp === "number" ? p.exp : null;
  } catch {
    return null;
  }
}

function load(): Stored | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const p: unknown = JSON.parse(raw);
    if (!isRecord(p) || typeof p.token !== "string" || typeof p.sub !== "string") return null;
    if (!ROLES.includes(p.role as Role)) return null;
    const exp = jwtExpiry(p.token);
    if (exp !== null && exp * 1000 < Date.now() + 5000) return null;
    return { token: p.token, role: p.role as Role, sub: p.sub };
  } catch {
    return null;
  }
}

interface SessionState {
  token: string | null;
  role: Role | null;
  sub: string | null;
  /** Why the user is on the splash (shown once). */
  notice: "expired" | null;
  login: (s: Stored) => void;
  logout: (reason?: "expired") => void;
}

const initial = load();

export const useSession = create<SessionState>((set) => ({
  token: initial?.token ?? null,
  role: initial?.role ?? null,
  sub: initial?.sub ?? null,
  notice: null,
  login: (s) => {
    try {
      sessionStorage.setItem(KEY, JSON.stringify(s));
    } catch {
      /* private mode: memory only */
    }
    set({ token: s.token, role: s.role, sub: s.sub, notice: null });
  },
  logout: (reason) => {
    try {
      sessionStorage.removeItem(KEY);
    } catch {
      /* ignore */
    }
    set({ token: null, role: null, sub: null, notice: reason ?? null });
  },
}));

export const canControl = (role: Role | null) => role === "officer" || role === "admin";
export const canResolve = (role: Role | null) => role === "analyst" || role === "admin";
export const canSeeOfficerViews = (role: Role | null) => role === "officer" || role === "analyst" || role === "admin";
