import { isRecord } from "@/types/guards";

/** A number that remembers its JSON source text (so 1.0 hashes as "1.0", as the ledger does). */
export class RawNumber {
  constructor(
    readonly value: number,
    readonly source: string,
  ) {}
}

export interface LedgerEntry {
  seq: number;
  ts: string;
  service: string;
  actor: string;
  event_type: string;
  payload_hash: string;
  prev_hash: string;
  entry_hash: string;
  model_version: string;
  payload: unknown;
  case_refs: string[];
  payload_present: boolean;
  /** Canonical JSON of the payload as the ledger hashes it; null if it cannot be reproduced here. */
  canonical: string | null;
}

export interface Checkpoint {
  checkpoint_id: string;
  seq: number;
  entry_hash: string;
  count: number;
  ts: string;
  key_id: string;
  signature_b64: string;
}

export interface LedgerPage {
  items: LedgerEntry[];
  next_from_seq: number | null;
  simulated_clock: boolean;
}

const str = (v: unknown): v is string => typeof v === "string";
const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Does this runtime give JSON.parse revivers the source text of primitives? */
export function supportsSourceText(): boolean {
  try {
    let ok = false;
    JSON.parse("1.0", (_k, v, ctx?: { source?: string }) => {
      ok = ctx?.source === "1.0";
      return v;
    });
    return ok;
  } catch {
    return false;
  }
}

/** Parse JSON keeping each number's source text. Returns the tree with RawNumber leaves. */
export function parseWithSource(text: string): unknown {
  return JSON.parse(text, function (_k: string, v: unknown, ctx?: { source?: string }) {
    if (typeof v === "number" && ctx?.source !== undefined) return new RawNumber(v, ctx.source);
    return v;
  });
}

/** Replace RawNumber leaves by plain numbers. */
export function toPlain(v: unknown): unknown {
  if (v instanceof RawNumber) return v.value;
  if (Array.isArray(v)) return v.map(toPlain);
  if (isRecord(v)) {
    const o: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v)) o[k] = toPlain(x);
    return o;
  }
  return v;
}

/** The ledger's canonical JSON: sorted keys, compact separators, UTF-8 (verify.py canonical_json). */
export function canonicalJson(v: unknown): string {
  if (v instanceof RawNumber) return v.source;
  if (v === null) return "null";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : JSON.stringify(v);
  if (typeof v === "string" || typeof v === "boolean") return JSON.stringify(v);
  if (Array.isArray(v)) return `[${v.map(canonicalJson).join(",")}]`;
  if (isRecord(v)) {
    const keys = Object.keys(v).sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(v[k])}`).join(",")}}`;
  }
  return "null";
}

export async function sha256Hex(s: string): Promise<string | null> {
  try {
    const subtle = globalThis.crypto?.subtle;
    if (!subtle) return null;
    const d = await subtle.digest("SHA-256", new TextEncoder().encode(s));
    return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
  } catch {
    return null;
  }
}

export type HashCheck = { state: "match" | "mismatch" | "unavailable" | "withheld"; recomputed?: string };

/** Compare the entry's payload_hash with SHA-256 of its canonical payload. */
export async function checkPayloadHash(e: Pick<LedgerEntry, "canonical" | "payload_hash" | "payload_present">): Promise<HashCheck> {
  if (!e.payload_present) return { state: "withheld" };
  if (e.canonical === null) return { state: "unavailable" };
  const h = await sha256Hex(e.canonical);
  if (h === null) return { state: "unavailable" };
  return h === e.payload_hash ? { state: "match", recomputed: h } : { state: "mismatch", recomputed: h };
}

/** Narrow one ledger entries response (raw text) to typed entries. Unknown fields are ignored. */
export function parseLedgerPage(text: string): LedgerPage {
  const withSource = supportsSourceText();
  const tree: unknown = withSource ? parseWithSource(text) : JSON.parse(text);
  const plain = withSource ? toPlain(tree) : tree;
  const out: LedgerPage = { items: [], next_from_seq: null, simulated_clock: false };
  if (!isRecord(plain) || !Array.isArray(plain.items)) return out;
  const rawItems = withSource && isRecord(tree) && Array.isArray(tree.items) ? tree.items : [];
  plain.items.forEach((r: unknown, i: number) => {
    if (!isRecord(r)) return;
    if (!num(r.seq) || !str(r.entry_hash) || !str(r.prev_hash) || !str(r.payload_hash)) return;
    const raw = rawItems[i];
    const rawPayload = isRecord(raw) ? raw.payload : undefined;
    const present = r.payload_present !== false && r.payload !== undefined && r.payload !== null;
    out.items.push({
      seq: r.seq,
      ts: str(r.ts) ? r.ts : "",
      service: str(r.service) ? r.service : "",
      actor: str(r.actor) ? r.actor : "",
      event_type: str(r.event_type) ? r.event_type : "",
      payload_hash: r.payload_hash,
      prev_hash: r.prev_hash,
      entry_hash: r.entry_hash,
      model_version: str(r.model_version) ? r.model_version : "",
      payload: present ? r.payload : null,
      case_refs: Array.isArray(r.case_refs) ? r.case_refs.filter(str) : [],
      payload_present: present,
      canonical: present && withSource && rawPayload !== undefined ? canonicalJson(rawPayload) : null,
    });
  });
  out.next_from_seq = num(plain.next_from_seq) ? plain.next_from_seq : null;
  out.simulated_clock = plain.simulated_clock === true;
  return out;
}

export function parseCheckpoints(v: unknown): Checkpoint[] {
  if (!isRecord(v)) return [];
  const list = Array.isArray(v.items) ? v.items : isRecord(v.checkpoint) ? [v.checkpoint] : [];
  const out: Checkpoint[] = [];
  for (const c of list) {
    if (!isRecord(c) || !str(c.checkpoint_id) || !num(c.seq)) continue;
    out.push({
      checkpoint_id: c.checkpoint_id,
      seq: c.seq,
      entry_hash: str(c.entry_hash) ? c.entry_hash : "",
      count: num(c.count) ? c.count : 0,
      ts: str(c.ts) ? c.ts : "",
      key_id: str(c.key_id) ? c.key_id : "",
      signature_b64: str(c.signature_b64) ? c.signature_b64 : "",
    });
  }
  return out.sort((a, b) => a.seq - b.seq);
}

/** Default case refs from the run: the first held transfer's own refs (what POST /cases defaults to). */
export function defaultCaseRefs(entries: readonly LedgerEntry[]): string[] {
  const hold = entries.find((e) => e.event_type === "hold.created" && e.case_refs.length > 0);
  return hold ? hold.case_refs.slice(0, 5) : [];
}

const REF_RE = /^[A-Za-z0-9_:.-]{3,96}$/;
export function validRef(r: string): boolean {
  return REF_RE.test(r);
}
export const CASE_ID_RE = /^[a-z0-9][a-z0-9_-]{1,47}$/;
