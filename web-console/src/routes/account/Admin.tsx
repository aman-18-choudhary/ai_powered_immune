import { useState } from "react";
import { Activity, CheckCircle2, CircleSlash, ScrollText, ServerCrash, Users, XCircle } from "lucide-react";
import { EmptyState, Panel } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ErrorBlock, LoadingBlock, PageHeader, Pager, Spinner, inputCls } from "@/components/complaints/ui";
import { complaintsApi, messageFor } from "@/lib/complaints/api";
import { ROLE_LABEL, formatDateTime, humanise, shortId } from "@/lib/complaints/format";
import { ACCOUNT_ROLES, type AccountRole, type AdminUser } from "@/lib/complaints/types";
import { useResource } from "@/lib/complaints/useResource";
import { usePageTitle } from "@/lib/hooks/usePageTitle";
import { useAccount } from "@/state/account";
import { cn } from "@/lib/utils/cn";

const LIMIT = 20;

type Pending = { kind: "role"; user: AdminUser; role: AccountRole } | { kind: "active"; user: AdminUser; active: boolean };

function UsersTab() {
  const me = useAccount((s) => s.session?.user.id);
  const [offset, setOffset] = useState(0);
  const [roleFilter, setRoleFilter] = useState("");
  const [choice, setChoice] = useState<Record<string, AccountRole>>({});
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ id: string; msg: string } | null>(null);
  const [announce, setAnnounce] = useState("");
  const res = useResource((s) => complaintsApi.adminUsers({ limit: LIMIT, offset, role: roleFilter || undefined }, s), `${offset}:${roleFilter}`);

  const confirm = async () => {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      const updated =
        pending.kind === "role"
          ? await complaintsApi.adminSetRole(pending.user.id, pending.role, pending.user.role)
          : await complaintsApi.adminSetActive(pending.user.id, pending.active);
      if (res.data) res.setData({ ...res.data, items: res.data.items.map((u) => (u.id === updated.id ? updated : u)) });
      setAnnounce(
        pending.kind === "role"
          ? `${updated.email ?? "User"} is now ${ROLE_LABEL[updated.role]}.`
          : `${updated.email ?? "User"} is now ${updated.is_active ? "active" : "deactivated"}.`,
      );
      setChoice((c) => {
        const n = { ...c };
        delete n[updated.id];
        return n;
      });
    } catch (e) {
      setError({ id: pending.user.id, msg: messageFor(e) });
    } finally {
      setBusy(false);
      setPending(null);
    }
  };

  return (
    <Panel
      title={res.data ? `Users (${res.data.total})` : "Users"}
      icon={<Users className="h-3.5 w-3.5" aria-hidden />}
      actions={
        <select
          aria-label="Filter by role"
          value={roleFilter}
          onChange={(e) => {
            setRoleFilter(e.target.value);
            setOffset(0);
          }}
          className={cn(inputCls, "h-8 w-auto py-0 text-xs")}
        >
          <option value="">All roles</option>
          {ACCOUNT_ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABEL[r]}
            </option>
          ))}
        </select>
      }
      data-testid="admin-users"
    >
      <p className="sr-only" role="status" aria-live="polite">
        {announce}
      </p>
      {res.loading && !res.data ? (
        <LoadingBlock label="Loading users" />
      ) : res.error && !res.data ? (
        <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />
      ) : res.data && res.data.items.length === 0 ? (
        <EmptyState icon={<Users className="h-5 w-5" aria-hidden />} title="No users" />
      ) : (
        <ul className="divide-y divide-line">
          {res.data?.items.map((u) => {
            const want = choice[u.id] ?? u.role;
            return (
              <li key={u.id} className="space-y-2 px-4 py-3" data-testid="admin-user">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="min-w-0 truncate text-sm font-medium">{u.email ?? "(no email)"}</span>
                  <span className={cn("text-[11px]", u.email_verified ? "text-safe" : "text-warn")}>{u.email_verified ? "verified" : "email not verified"}</span>
                  <span className="rounded-md border border-line bg-chip px-1.5 py-0.5 text-[11px] font-medium" data-testid="admin-user-role">
                    {ROLE_LABEL[u.role]}
                    {u.role_source ? ` · via ${u.role_source}` : ""}
                  </span>
                  <span className={cn("inline-flex items-center gap-1 text-[11px]", u.is_active ? "text-safe" : "text-danger-text")}>
                    {u.is_active ? <CheckCircle2 className="h-3 w-3" aria-hidden /> : <CircleSlash className="h-3 w-3" aria-hidden />}
                    {u.is_active ? "Active" : "Deactivated"}
                  </span>
                  {u.id === me && <span className="text-[11px] text-subtle">(you)</span>}
                </div>
                <p className="text-xs text-subtle">
                  {u.display_name ? `${u.display_name} · ` : ""}joined {formatDateTime(u.created_at)} · last sign-in {formatDateTime(u.last_login_at)} · <span className="mono">{shortId(u.id)}</span>
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <label className="sr-only" htmlFor={`role-${u.id}`}>
                    New role for {u.email ?? u.id}
                  </label>
                  <select id={`role-${u.id}`} value={want} onChange={(e) => setChoice((c) => ({ ...c, [u.id]: e.target.value as AccountRole }))} className={cn(inputCls, "h-8 w-auto py-0 text-xs")} data-testid="admin-role-select">
                    {ACCOUNT_ROLES.map((r) => (
                      <option key={r} value={r}>
                        {ROLE_LABEL[r]}
                      </option>
                    ))}
                  </select>
                  <Button size="sm" variant="secondary" disabled={want === u.role} onClick={() => setPending({ kind: "role", user: u, role: want })} data-testid="admin-role-change">
                    Change role
                  </Button>
                  <Button size="sm" variant={u.is_active ? "danger" : "safe"} onClick={() => setPending({ kind: "active", user: u, active: !u.is_active })} data-testid="admin-toggle-active">
                    {u.is_active ? "Deactivate" : "Activate"}
                  </Button>
                </div>
                {error?.id === u.id && (
                  <p role="alert" className="rounded-lg border border-danger/40 bg-danger-soft p-2 text-sm" data-testid="admin-error">
                    {error.msg}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {res.data && <Pager total={res.data.total} limit={LIMIT} offset={offset} onChange={setOffset} label="Users" />}
      <Dialog open={pending !== null} onOpenChange={(o) => !o && !busy && setPending(null)}>
        <DialogContent className="w-[min(94vw,460px)]">
          <DialogTitle className="text-lg font-semibold">{pending?.kind === "role" ? "Change role?" : pending?.kind === "active" && pending.active ? "Activate account?" : "Deactivate account?"}</DialogTitle>
          <DialogDescription className="mt-2 text-sm text-muted">
            {pending?.kind === "role" &&
              `${pending.user.email ?? "This user"} will change from ${ROLE_LABEL[pending.user.role]} to ${ROLE_LABEL[pending.role]}. The change is recorded in the audit log.`}
            {pending?.kind === "active" &&
              (pending.active
                ? `${pending.user.email ?? "This user"} will be able to sign in and use the service again.`
                : `${pending.user.email ?? "This user"} will be refused on every request until reactivated. Nothing is deleted.`)}
          </DialogDescription>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setPending(null)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void confirm()} disabled={busy} data-testid="admin-confirm">
              {busy && <Spinner />} Confirm
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </Panel>
  );
}

function AuditTab() {
  const [offset, setOffset] = useState(0);
  const res = useResource((s) => complaintsApi.adminAudit({ limit: LIMIT, offset }, s), String(offset));
  const show = (v: unknown) => (v === null || v === undefined ? "–" : typeof v === "string" ? v : JSON.stringify(v));
  return (
    <Panel title={res.data ? `Audit log (${res.data.total})` : "Audit log"} icon={<ScrollText className="h-3.5 w-3.5" aria-hidden />} data-testid="admin-audit">
      {res.loading && !res.data ? (
        <LoadingBlock label="Loading audit log" />
      ) : res.error && !res.data ? (
        <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />
      ) : res.data && res.data.items.length === 0 ? (
        <EmptyState icon={<ScrollText className="h-5 w-5" aria-hidden />} title="No audit entries yet" />
      ) : (
        <ul className="divide-y divide-line">
          {res.data?.items.map((r) => (
            <li key={r.id} className="space-y-0.5 px-4 py-2.5 text-sm" data-testid="audit-row">
              <p>
                <strong>{humanise(r.action)}</strong> <span className="text-xs text-subtle">{formatDateTime(r.created_at)}</span>
              </p>
              <p className="text-xs text-muted">
                actor <span className="mono">{r.actor_user_id ? shortId(r.actor_user_id) : "system"}</span>
                {r.target_user_id && (
                  <>
                    {" "}
                    · target <span className="mono">{shortId(r.target_user_id)}</span>
                  </>
                )}
                {(r.before !== null || r.after !== null) && (
                  <>
                    {" "}
                    · <span className="mono break-all">{show(r.before)}</span> → <span className="mono break-all">{show(r.after)}</span>
                  </>
                )}
              </p>
            </li>
          ))}
        </ul>
      )}
      {res.data && <Pager total={res.data.total} limit={LIMIT} offset={offset} onChange={setOffset} label="Audit log" />}
    </Panel>
  );
}

const GOOD = new Set(["ok", "importable", "reachable", "available", "loaded", "configured"]);
const OFF = new Set(["not_configured", "demo_mode"]);

function HealthTab() {
  const res = useResource((s) => complaintsApi.adminHealth(s), "health");
  return (
    <Panel title="Service health" icon={<Activity className="h-3.5 w-3.5" aria-hidden />} actions={<Button size="sm" variant="ghost" onClick={res.reload}>Refresh</Button>} data-testid="admin-health">
      {res.loading && !res.data ? (
        <LoadingBlock label="Checking services" />
      ) : res.error && !res.data ? (
        <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />
      ) : res.data ? (
        <ul className="divide-y divide-line">
          {Object.entries(res.data).map(([name, info]) => {
            const s = String(info?.status ?? "unknown");
            const good = GOOD.has(s);
            const off = OFF.has(s);
            const Icon = good ? CheckCircle2 : off ? CircleSlash : s === "error" || s === "unreachable" ? ServerCrash : XCircle;
            const extra = Object.entries(info ?? {}).filter(([k, v]) => k !== "status" && v !== null && typeof v !== "object");
            return (
              <li key={name} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-sm" data-testid="health-row">
                <Icon className={cn("h-4 w-4 shrink-0", good ? "text-safe" : off ? "text-subtle" : "text-danger-text")} aria-hidden />
                <span className="min-w-[140px] font-medium">{humanise(name)}</span>
                <span className={cn("text-xs", good ? "text-safe" : off ? "text-muted" : "text-danger-text")}>
                  {good ? "Working" : s === "not_configured" ? "Not configured" : s === "demo_mode" ? "Demo / local sign-in mode" : "Unavailable"} ({s})
                </span>
                {extra.length > 0 && <span className="text-xs text-subtle">{extra.map(([k, v]) => `${humanise(k)}: ${String(v)}`).join(" · ")}</span>}
              </li>
            );
          })}
        </ul>
      ) : null}
    </Panel>
  );
}

export default function Admin() {
  usePageTitle("Administration");
  return (
    <div className="mx-auto max-w-5xl space-y-5 px-4 py-6 md:px-6">
      <PageHeader title="Administration" subtitle="Users and roles, the audit log and service health. Every change is checked and recorded by the complaints service." />
      <Tabs defaultValue="users" className="space-y-4">
        <TabsList aria-label="Administration sections">
          <TabsTrigger value="users">Users</TabsTrigger>
          <TabsTrigger value="audit">Audit log</TabsTrigger>
          <TabsTrigger value="health">Health</TabsTrigger>
        </TabsList>
        <TabsContent value="users">
          <UsersTab />
        </TabsContent>
        <TabsContent value="audit">
          <AuditTab />
        </TabsContent>
        <TabsContent value="health">
          <HealthTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
