import { Link, useLocation, useParams } from "react-router-dom";
import { ArrowLeft, CheckCircle2 } from "lucide-react";
import { ComplaintDetailView } from "@/components/complaints/ComplaintDetailView";
import { ErrorBlock, LoadingBlock, StatusChip } from "@/components/complaints/ui";
import { UploadList } from "@/components/complaints/UploadList";
import { complaintsApi } from "@/lib/complaints/api";
import { formatDateTime } from "@/lib/complaints/format";
import type { UploadItem } from "@/lib/complaints/upload";
import { useResource } from "@/lib/complaints/useResource";
import { usePageTitle } from "@/lib/hooks/usePageTitle";
import { isStaff, useAccountRole } from "@/state/account";

interface CreatedState {
  created?: boolean;
  uploads?: UploadItem[];
}

export default function ComplaintPage() {
  const { id = "" } = useParams();
  const role = useAccountRole();
  const loc = useLocation();
  const st = (loc.state ?? {}) as CreatedState;
  const res = useResource((s) => complaintsApi.get(id, s), id);
  usePageTitle(res.data ? res.data.title : "Complaint");
  const staff = isStaff(role);
  const back = staff ? { to: "/investigator", label: "Investigator queue" } : { to: "/me", label: "My complaints" };

  return (
    <div className="mx-auto max-w-6xl space-y-5 px-4 py-6 md:px-6">
      <Link to={back.to} className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
        <ArrowLeft className="h-4 w-4" aria-hidden /> {back.label}
      </Link>
      {st.created && res.data && (
        <section role="status" aria-live="polite" className="space-y-3 rounded-xl border border-safe/40 bg-safe-soft p-4" data-testid="created-banner">
          <p className="flex flex-wrap items-center gap-2 text-sm">
            <CheckCircle2 className="h-4 w-4 text-safe" aria-hidden />
            <strong>Complaint registered.</strong> ID <span className="mono">{res.data.id}</span> · status <StatusChip status={res.data.status} /> · reported{" "}
            {formatDateTime(res.data.reported_at)}
          </p>
          <p className="text-xs text-muted">Keep the ID for your records. Also report at cybercrime.gov.in or call 1930 if money was lost.</p>
          {st.uploads && st.uploads.length > 0 && <UploadList items={st.uploads} />}
        </section>
      )}
      {res.loading && !res.data ? (
        <LoadingBlock label="Loading complaint" />
      ) : res.error && !res.data ? (
        <ErrorBlock
          message={res.errorStatus === 404 ? "This complaint does not exist or is not available to your account." : res.error}
          onRetry={res.errorStatus === 404 ? undefined : res.reload}
          unavailable={res.unavailable}
        />
      ) : res.data && role ? (
        <ComplaintDetailView complaint={res.data} role={role} onReload={res.reload} onReplace={res.setData} />
      ) : null}
    </div>
  );
}
