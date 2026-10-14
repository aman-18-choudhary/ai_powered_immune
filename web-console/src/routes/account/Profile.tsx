import { useEffect, useState, type FormEvent } from "react";
import { Save, ShieldCheck, UserRound } from "lucide-react";
import { Panel } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { ErrorBlock, Field, LoadingBlock, PageHeader, Spinner, inputCls } from "@/components/complaints/ui";
import { ComplaintsApiError, complaintsApi, messageFor } from "@/lib/complaints/api";
import { ROLE_LABEL, formatDateTime } from "@/lib/complaints/format";
import { useResource } from "@/lib/complaints/useResource";
import { usePageTitle } from "@/lib/hooks/usePageTitle";

export default function Profile() {
  usePageTitle("Profile and privacy");
  const res = useResource((s) => complaintsApi.me(s), "me");
  const [name, setName] = useState("");
  const [nameErr, setNameErr] = useState<string | null>(null);
  const [saving, setSaving] = useState<"name" | "privacy" | null>(null);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (res.data) setName(res.data.display_name ?? "");
  }, [res.data]);

  const saveName = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setMsg("");
    const trimmed = name.trim();
    if (trimmed.length > 100) {
      setNameErr("Keep the name under 100 characters.");
      return;
    }
    setNameErr(null);
    setSaving("name");
    try {
      // an empty name returns to the name from sign-in (null)
      const me = await complaintsApi.updateMe({ display_name: trimmed || null });
      res.setData(me);
      setMsg(trimmed ? "Display name saved." : "Display name reset to the name from sign-in.");
    } catch (err) {
      if (err instanceof ComplaintsApiError && err.fieldErrors.display_name) setNameErr(err.fieldErrors.display_name);
      else setError(messageFor(err));
    } finally {
      setSaving(null);
    }
  };

  const setMatching = async (allow: boolean) => {
    setError(null);
    setMsg("");
    setSaving("privacy");
    try {
      const me = await complaintsApi.updateMe({ privacy: { allow_case_matching: allow } });
      res.setData(me);
      setMsg(allow ? "Case matching allowed." : "Case matching turned off.");
    } catch (err) {
      setError(messageFor(err));
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-5 px-4 py-6 md:px-6">
      <PageHeader title="Profile and privacy" subtitle="Your account details as stored by the complaints service." />
      <p role="status" aria-live="polite" className="text-sm text-safe" data-testid="profile-status">
        {msg}
      </p>
      {error && (
        <p role="alert" className="rounded-xl border border-danger/40 bg-danger-soft p-3 text-sm">
          {error}
        </p>
      )}
      {res.loading && !res.data ? (
        <LoadingBlock label="Loading profile" />
      ) : res.error && !res.data ? (
        <ErrorBlock message={res.error} onRetry={res.reload} unavailable={res.unavailable} />
      ) : res.data ? (
        <>
          <Panel title="Profile" icon={<UserRound className="h-3.5 w-3.5" aria-hidden />}>
            <div className="space-y-4 p-4">
              <dl className="grid gap-x-4 gap-y-1.5 text-sm sm:grid-cols-[160px_minmax(0,1fr)]">
                <dt className="text-muted">Email</dt>
                <dd className="break-words">
                  {res.data.email ?? "Not available"} {res.data.email_verified ? <span className="text-xs text-safe">(verified)</span> : <span className="text-xs text-warn">(not verified)</span>}
                </dd>
                <dt className="text-muted">Role</dt>
                <dd data-testid="profile-role">{ROLE_LABEL[res.data.role]}</dd>
                <dt className="text-muted">Member since</dt>
                <dd>{formatDateTime(res.data.created_at)}</dd>
              </dl>
              {res.data.message && (
                <p role="status" className="rounded-lg border border-warn/40 bg-warn-soft p-2 text-sm">
                  {res.data.message}
                </p>
              )}
              <form onSubmit={saveName} className="flex flex-wrap items-end gap-3" noValidate>
                <Field label="Display name" optional hint="Leave empty to use the name from sign-in." error={nameErr ?? undefined} className="min-w-[220px] flex-1">
                  {(p) => <input {...p} maxLength={100} value={name} onChange={(e) => setName(e.target.value)} className={inputCls} data-testid="display-name" />}
                </Field>
                <Button type="submit" variant="secondary" disabled={saving !== null} data-testid="save-name">
                  {saving === "name" ? <Spinner /> : <Save className="h-4 w-4" aria-hidden />} Save
                </Button>
              </form>
            </div>
          </Panel>
          <Panel title="Privacy" icon={<ShieldCheck className="h-3.5 w-3.5" aria-hidden />}>
            <div className="flex items-start justify-between gap-4 p-4">
              <div className="space-y-1 text-sm">
                <label htmlFor="allow-matching" className="font-medium">
                  Allow my complaints to be used for case matching
                </label>
                <p id="allow-matching-hint" className="text-muted">
                  When on, details of your complaints may later be compared with other complaints to find the same scammers (for example a shared
                  phone number or UPI ID). When off, your complaints are to be left out of matching shown to others. Investigators can still read your
                  complaints either way.
                </p>
                <p className="text-xs text-subtle">The setting is saved now; matching itself is not built yet and will honour it when it is.</p>
              </div>
              <div className="flex items-center gap-2 pt-1">
                {saving === "privacy" && <Spinner />}
                <Switch
                  id="allow-matching"
                  aria-describedby="allow-matching-hint"
                  checked={res.data.privacy.allow_case_matching}
                  onCheckedChange={(v) => void setMatching(v)}
                  disabled={saving !== null}
                  data-testid="allow-matching"
                />
              </div>
            </div>
          </Panel>
        </>
      ) : null}
    </div>
  );
}
