// @vitest-environment node
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { canonicalJson, checkPayloadHash, defaultCaseRefs, parseCheckpoints, parseLedgerPage, parseWithSource, supportsSourceText, validRef } from "./ledger";

const text = readFileSync(resolve(__dirname, "../../mocks/ledger.json"), "utf8");

describe("ledger parsing and hash recomputation", () => {
  it("recomputes every payload_hash of the recorded ledger exactly (canonical JSON incl. 1.0 floats)", async () => {
    expect(supportsSourceText()).toBe(true);
    const page = parseLedgerPage(text);
    expect(page.items.length).toBeGreaterThanOrEqual(12);
    expect(page.simulated_clock).toBe(true);
    for (const e of page.items) {
      const c = await checkPayloadHash(e);
      expect(c.state, `seq ${e.seq}`).toBe("match");
    }
  });

  it("keeps the hash chain links: prev_hash is the previous entry_hash", () => {
    const page = parseLedgerPage(text);
    for (let i = 1; i < page.items.length; i++) expect(page.items[i]!.prev_hash).toBe(page.items[i - 1]!.entry_hash);
    expect(page.items[0]!.prev_hash).toMatch(/^0{64}$/);
  });

  it("canonical JSON sorts keys, drops spaces and preserves the float source", () => {
    const tree = parseWithSource('{"b":1.0,"a":[2,{"z":"é","y":0.85}],"c":null}');
    expect(canonicalJson(tree)).toBe('{"a":[2,{"y":0.85,"z":"é"}],"b":1.0,"c":null}');
  });

  it("flags a tampered payload as a mismatch and a withheld one as withheld", async () => {
    const e = parseLedgerPage(text).items[0]!;
    const bad = { ...e, canonical: e.canonical!.replace("1.0", "0.1") };
    expect((await checkPayloadHash(bad)).state).toBe("mismatch");
    expect((await checkPayloadHash({ ...e, payload_present: false })).state).toBe("withheld");
  });

  it("ignores malformed rows and unknown fields", () => {
    const p = parseLedgerPage(JSON.stringify({ items: [{ seq: "x" }, null, { seq: 1, entry_hash: "a", prev_hash: "b", payload_hash: "c", extra: 1 }] }));
    expect(p.items).toHaveLength(1);
    expect(parseLedgerPage("{}").items).toEqual([]);
  });

  it("derives default case refs from the first hold and validates refs", () => {
    const refs = defaultCaseRefs(parseLedgerPage(text).items);
    expect(refs.length).toBe(3);
    expect(refs.some((r) => r.startsWith("txn_"))).toBe(true);
    expect(refs.every(validRef)).toBe(true);
    expect(validRef("bad ref!")).toBe(false);
  });

  it("parses checkpoints from list and single forms", () => {
    const cp = { checkpoint_id: "cp-1", seq: 4, entry_hash: "h", count: 4, ts: "t", key_id: "k", signature_b64: "s" };
    expect(parseCheckpoints({ items: [cp] })).toHaveLength(1);
    expect(parseCheckpoints({ checkpoint: cp })).toHaveLength(1);
    expect(parseCheckpoints({ checkpoint: null })).toEqual([]);
  });
});
