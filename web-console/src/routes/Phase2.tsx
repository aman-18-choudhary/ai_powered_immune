import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import { BookOpenCheck, FlaskConical, Gauge, Hourglass, ShieldCheck, Smartphone, type LucideIcon } from "lucide-react";
import { Panel, Skeleton } from "@/components/common/Panel";
import { Button } from "@/components/ui/button";
import { listContainer, listItem } from "@/lib/motion/variants";

type PageKey = "analyst" | "evidence" | "citizen" | "insights" | "about";

const PAGES: Record<PageKey, { title: string; Icon: LucideIcon; lead: string; items: string[]; api: string }> = {
  analyst: {
    title: "Analyst Console",
    Icon: ShieldCheck,
    lead: "A working queue for the humans in the loop.",
    items: [
      "Hold queue sorted by deadline, each with a countdown ring and reason chips",
      "Resolve actions (confirm mule / release) with a confirmation step and idempotent retries",
      "Diff view when a hold is upgraded; keyboard shortcuts for triage",
    ],
    api: "GET /api/holds, POST /api/holds/{txn_ref}/resolve",
  },
  evidence: {
    title: "Evidence",
    Icon: BookOpenCheck,
    lead: "A hash chain you can verify without trusting the server.",
    items: [
      "Animated hash chain with an entry inspector and signed checkpoints",
      "Package export and the offline verifier result (PASS / UNPINNED / FAIL)",
      "Redaction view and an honest-limits panel",
    ],
    api: "GET /api/ledger/*, GET /api/packages/{case}, POST /api/verify",
  },
  citizen: {
    title: "Citizen Shield",
    Icon: Smartphone,
    lead: "A mobile-first assistant: paste a message, get a plain verdict.",
    items: [
      "Language picker (English, Hindi, Tamil, Bengali)",
      "One large verdict card with reasons, next steps and honest uncertainty copy",
      "Report action; nothing you paste is stored",
    ],
    api: "POST /api/citizen/assess",
  },
  insights: {
    title: "Insights",
    Icon: Gauge,
    lead: "The benchmark, with its limits always in view.",
    items: [
      "Headline precision, recall and FPR with confidence intervals",
      "Per-rail FPR table, ablations (no antibody / no call signal), delay sensitivity",
      "Limitations, seeds and commit for reproducibility",
    ],
    api: "GET /api/benchmark/summary",
  },
  about: {
    title: "About",
    Icon: FlaskConical,
    lead: "What is real, what is simulated, and where the limits are.",
    items: [
      "Architecture diagram of the single-process composition",
      "A plain table of real code paths vs simulated inputs",
      "Honest limits of the benchmark and the hero case",
    ],
    api: "static",
  },
};

export default function Phase2({ page }: { page: PageKey }) {
  const p = PAGES[page];
  return (
    <div className="mx-auto max-w-5xl px-4 py-8 md:px-8">
      <motion.div variants={listContainer} initial="hidden" animate="show" className="grid gap-4 md:grid-cols-[1.4fr_1fr]">
        <motion.div variants={listItem}>
          <Panel blur className="h-full p-0" bodyClassName="p-6">
            <div className="flex items-center gap-3">
              <span className="grid h-12 w-12 place-items-center rounded-2xl border border-line bg-intel-soft text-intel">
                <p.Icon className="h-6 w-6" aria-hidden />
              </span>
              <div>
                <p className="inline-flex items-center gap-1.5 rounded-full border border-warn/40 bg-warn-soft px-2 py-0.5 text-[11px] font-semibold text-warn">
                  <Hourglass className="h-3 w-3" aria-hidden /> Coming in phase F2
                </p>
                <h1 className="mt-1 text-2xl font-semibold tracking-tight">{p.title}</h1>
              </div>
            </div>
            <p className="mt-4 text-base text-muted">{p.lead}</p>
            <ul className="mt-4 space-y-2.5 text-sm">
              {p.items.map((t) => (
                <li key={t} className="flex gap-2.5">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-intel" aria-hidden />
                  <span>{t}</span>
                </li>
              ))}
            </ul>
            <p className="mono mt-5 text-xs text-subtle">Backed by: {p.api}</p>
            <div className="mt-6 flex flex-wrap gap-2">
              <Link to="/">
                <Button variant="primary">Watch the live interception</Button>
              </Link>
              <Link to="/command">
                <Button variant="secondary">Open the command centre</Button>
              </Link>
            </div>
          </Panel>
        </motion.div>
        <motion.div variants={listItem}>
          <Panel title="Preview" className="h-full" bodyClassName="space-y-3 p-4" aria-hidden>
            <Skeleton className="h-6 w-1/2" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-10 w-2/3" />
          </Panel>
        </motion.div>
      </motion.div>
    </div>
  );
}
