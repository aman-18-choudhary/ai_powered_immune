export interface ArchNode {
  id: string;
  label: string;
  sub: string;
  x: number;
  y: number;
  w: number;
  h: number;
  tone: "real" | "sim" | "ui";
  desc: string;
}
export interface ArchEdge {
  id: string;
  from: string;
  to: string;
  label: string;
  /** SVG path in diagram coordinates. */
  d: string;
}

/** Diagram geometry is 900 x 470. The bus is a horizontal bar; services hang above and below it. */
export const ARCH_NODES: ArchNode[] = [
  { id: "sim", label: "Simulator", sub: "sim-engine", x: 24, y: 24, w: 168, h: 74, tone: "sim",
    desc: "Generates the synthetic world: citizens, accounts, calls, transactions and the constructed hero scenario. Everything upstream of the services is made up." },
  { id: "call", label: "Call-guard", sub: "scores call transcripts", x: 232, y: 24, w: 168, h: 74, tone: "real",
    desc: "Real session scorer (rules plus classifier). Raises a call alert when the running risk crosses the threshold." },
  { id: "txnA", label: "Txn-guard · bank A", sub: "holds and decisions", x: 440, y: 24, w: 200, h: 74, tone: "real",
    desc: "Real transaction scorer with policy overlays and a hold store. Holds the victim's transfer; an analyst confirms or releases it." },
  { id: "txnB", label: "Txn-guard · bank B", sub: "another bank, same code", x: 680, y: 24, w: 196, h: 74, tone: "real",
    desc: "A second bank running the same code. It never saw the scam call, yet blocks the mule when the antibody arrives." },
  { id: "bus", label: "Event bus (in-memory, one process)", sub: "", x: 24, y: 196, w: 852, h: 54, tone: "real",
    desc: "In this demo the bus is a single in-process queue. The Docker stack uses real messaging between containers; the contracts are the same." },
  { id: "hub", label: "Antibody hub", sub: "shared threat memory", x: 232, y: 348, w: 200, h: 74, tone: "real",
    desc: "Real antibody store and outbox. A confirmed mule becomes a privacy-preserving antibody that other banks apply within milliseconds." },
  { id: "ledger", label: "Evidence ledger", sub: "hash chain, Ed25519", x: 464, y: 348, w: 200, h: 74, tone: "real",
    desc: "Real append-only hash chain with signed checkpoints, case packages and an offline verifier. Timestamps use a simulated clock here." },
  { id: "api", label: "Demo server and console", sub: "REST + WebSocket", x: 696, y: 348, w: 180, h: 74, tone: "ui",
    desc: "FastAPI process composing the services, and this web console. The console only ever talks to the /api contract." },
];

export const ARCH_EDGES: ArchEdge[] = [
  { id: "e-sim-bus", from: "sim", to: "bus", label: "synthetic events", d: "M108 98 V196" },
  { id: "e-call-bus", from: "call", to: "bus", label: "call alerts", d: "M316 98 V196" },
  { id: "e-a-bus", from: "txnA", to: "bus", label: "decisions, holds", d: "M540 98 V196" },
  { id: "e-bus-b", from: "bus", to: "txnB", label: "antibody applied", d: "M778 196 V98" },
  { id: "e-bus-hub", from: "bus", to: "hub", label: "confirmed mule", d: "M332 250 V348" },
  { id: "e-hub-bus", from: "hub", to: "bus", label: "antibody published", d: "M300 348 V250" },
  { id: "e-bus-ledger", from: "bus", to: "ledger", label: "audit entries", d: "M564 250 V348" },
  { id: "e-ledger-api", from: "ledger", to: "api", label: "packages, verify", d: "M664 385 H696" },
  { id: "e-bus-api", from: "bus", to: "api", label: "stream events", d: "M820 250 V348" },
];

export const nodeById = (id: string) => ARCH_NODES.find((n) => n.id === id);

/** Edges touching a node: the hover highlight set. */
export function edgesOf(nodeId: string): ArchEdge[] {
  return ARCH_EDGES.filter((e) => e.from === nodeId || e.to === nodeId);
}

export interface RealRow {
  piece: string;
  real: string;
  simulated: string;
}
export const REAL_VS_SIMULATED: RealRow[] = [
  { piece: "Call scoring", real: "Real call-guard session scorer and its classifier (clf-v1), run in-process.", simulated: "Call scripts and speakers come from the simulator; no real call audio or numbers." },
  { piece: "Transaction scoring", real: "Real txn-guard model (gbm-v1), rules and policy overlays; real hold store and deadlines.", simulated: "Citizens, accounts, payees and transfers are synthetic; amounts reach the stream only as buckets." },
  { piece: "Shared threat memory", real: "Real antibody-hub store and outbox; bank B applies antibodies through its real consumer.", simulated: "Two banks in one process on an in-memory bus; no real banks, no network between them." },
  { piece: "Evidence ledger", real: "Real hash chain, Ed25519 signatures, signed checkpoints, case packages and the offline verifier.", simulated: "Timestamps use a simulated clock and the signing key is generated per process." },
  { piece: "Analyst decisions", real: "Real resolve API with decision_seq checks and role gating.", simulated: "Unless a person acts, the scenario auto-confirms the mule 60 simulated seconds after the hold." },
  { piece: "Benchmark", real: "The real evaluation harness over the same scorers (committed JSON, regenerated by command).", simulated: "Run entirely on simulator data, including the models' own training generators." },
  { piece: "Geography", real: "Static table of Indian cities used to place points.", simulated: "No coordinate of any individual; hotspots are synthetic." },
  { piece: "Access control", real: "JWT roles enforced on every route.", simulated: "Demo login has no passwords: anyone can choose any role." },
];

export const LIMITS: string[] = [
  "Every number is simulator-generated. None of it is evidence of real-world performance.",
  "The hero run is a constructed hard case (a seasoned mule and a call-guard miss). It shows what shared threat memory adds, not how often that situation occurs.",
  "Antibody poisoning, false confirmations and antibodies on legitimate merchants are not simulated.",
  "One process, one run at a time, in-memory history. The multi-container stack is the production-shaped packaging.",
  "The evidence chain proves integrity and, with a pinned key, authorship of the record. It does not prove the events were true or legally admissible, and is not legal advice.",
];

export const AUDIT_SUMMARY: { title: string; body: string }[] = [
  { title: "What is recorded", body: "Opaque ids, the decision and its sequence, score, reason codes, model version, rail, an amount bucket, deadlines and the acting role. Payloads are PII-free and at most 4 KiB." },
  { title: "What is only hashed", body: "Every entry carries sha256 of its canonical payload. A package may withhold a payload and still verify, because the chain binds the hash." },
  { title: "What is never recorded", body: "Amounts, account or phone numbers, names, device ids, call text and free-text notes. Emitters reject such payloads and the ledger re-checks them." },
  { title: "Audit never blocks a fraud action", body: "Alerts, holds and antibodies are published first; audit entries drain from outboxes with timeouts and circuit breakers." },
  { title: "Offline verification", body: "Obtain the package and the ledger public key separately, then run the shipped verify.py with plain Python. Exit 0 pass, 1 fail, 2 unpinned." },
];

export const STACK: { group: string; items: string[] }[] = [
  { group: "Console", items: ["React 18", "TypeScript (strict)", "Vite", "Tailwind CSS 4", "Radix UI", "Framer Motion", "d3-geo", "Zustand", "React Router"] },
  { group: "Services", items: ["Python 3.13", "FastAPI and uvicorn", "SQLite", "Ed25519 signatures", "scikit-learn style models (gbm-v1, clf-v1)"] },
  { group: "Quality", items: ["Vitest", "Playwright", "axe-core", "pytest", "ruff", "ESLint"] },
];
