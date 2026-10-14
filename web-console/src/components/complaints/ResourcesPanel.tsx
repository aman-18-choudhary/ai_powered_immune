import { ExternalLink, LifeBuoy, Phone } from "lucide-react";
import { Panel } from "@/components/common/Panel";

/** Static, factual guidance. Official resources only; no invented numbers. */
export function ResourcesPanel() {
  return (
    <Panel title="Safety guidance and official resources" icon={<LifeBuoy className="h-3.5 w-3.5" aria-hidden />} data-testid="resources">
      <div className="space-y-4 p-4 text-sm">
        <section aria-labelledby="res-official" className="space-y-2">
          <h3 id="res-official" className="text-xs font-semibold uppercase tracking-wide text-subtle">
            Official resources (Government of India)
          </h3>
          <ul className="space-y-2">
            <li className="flex items-start gap-2">
              <Phone className="mt-0.5 h-4 w-4 shrink-0 text-safe" aria-hidden />
              <span>
                <strong>National Cyber Crime Helpline: 1930.</strong> Call as soon as possible if money was lost; quick reporting improves the
                chance of a freeze.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <ExternalLink className="mt-0.5 h-4 w-4 shrink-0 text-safe" aria-hidden />
              <span>
                <strong>National Cyber Crime Reporting Portal:</strong>{" "}
                <a href="https://cybercrime.gov.in" target="_blank" rel="noopener noreferrer" className="text-flow underline underline-offset-2">
                  cybercrime.gov.in
                </a>
                . File the official complaint there; this service does not file it for you.
              </span>
            </li>
            <li className="flex items-start gap-2">
              <Phone className="mt-0.5 h-4 w-4 shrink-0 text-safe" aria-hidden />
              <span>
                <strong>Report to your bank immediately</strong> using the number on your card or your bank&apos;s official app, so it can block
                the card, UPI handle or transfer.
              </span>
            </li>
          </ul>
        </section>
        <section aria-labelledby="res-safety" className="space-y-1.5">
          <h3 id="res-safety" className="text-xs font-semibold uppercase tracking-wide text-subtle">
            Stay safe
          </h3>
          <ul className="list-disc space-y-1 pl-5 text-muted">
            <li>Never share an OTP, PIN, CVV or password, even with someone claiming to be from a bank or agency.</li>
            <li>No agency places anyone under &quot;digital arrest&quot; or asks for money over a call or video call.</li>
            <li>Keep screenshots, call logs, messages and transaction references; do not delete them.</li>
          </ul>
        </section>
      </div>
    </Panel>
  );
}
