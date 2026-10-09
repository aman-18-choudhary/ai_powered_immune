/** Static, accurate copy for the "Explain this step" popover and the honest-label strip. */
export interface StepExplain {
  what: string;
  why: string;
  real: string;
}

export const HONEST_LABEL =
  "Constructed hard case: seasoned mule account + call-guard miss; only the shared threat memory saves victim B.";

export const STEP_ORDER = [
  "call_begins",
  "call_flagged",
  "transfer_held",
  "analyst_confirms",
  "antibody_shared",
  "bank_b_blocks",
  "cash_out",
  "evidence_sealed",
] as const;

export const STEP_SHORT: Record<string, string> = {
  call_begins: "Call",
  call_flagged: "Flagged",
  transfer_held: "Held",
  analyst_confirms: "Confirmed",
  antibody_shared: "Shared",
  bank_b_blocks: "B blocks",
  bank_b_misses: "B misses",
  analyst_released: "Released",
  cash_out: "Cash-out",
  evidence_sealed: "Sealed",
};

export const EXPLAIN: Record<string, StepExplain> = {
  call_begins: {
    what: "A simulated caller poses as an investigator and pressures Victim A with a digital-arrest script.",
    why: "The call is the earliest signal. Call-guard scores each utterance as it arrives.",
    real: "Real: the call-guard scorer runs on every line. Simulated: the caller, the script and the victim.",
  },
  call_flagged: {
    what: "The session score crosses the alert threshold (0.7) from a known-script match plus legal-threat and authority cues.",
    why: "A call alone never moves or blocks money; it raises the risk that transaction scoring uses.",
    real: "Real: the alert is written to the evidence ledger. Simulated: the call text.",
  },
  transfer_held: {
    what: "Victim A starts a UPI transfer to a new payee. Bank A combines the active call risk, an amount far above this payer's norm and the new payee, and floors the decision to hold for verification.",
    why: "The money does not move while the hold is open; an analyst has a deadline to decide.",
    real: "Real: txn-guard's policy floor. Simulated: the payer, payee and amount.",
  },
  analyst_confirms: {
    what: "An analyst confirms the payee is a mule. In this demo that happens automatically 60 simulated seconds after the hold, or immediately if an analyst or admin presses Confirm.",
    why: "A human-confirmed label is what allows a mule to be shared with other banks.",
    real: "Real: the confirm path and its ledger entry. Simulated: the automatic analyst.",
  },
  antibody_shared: {
    what: "The confirmed mule becomes an antibody: a keyed hash of the payee, not a name or number. It is published and applied at other banks.",
    why: "Sharing the label (not the customer data) is how one bank's detection protects every bank.",
    real: "Real: the publication-to-application latency shown is measured in this process.",
  },
  bank_b_blocks: {
    what: "Victim B's bank sees a transfer to the same mule. B's call-guard missed this call and the payee looks new to B, but the antibody match floors the decision to hold.",
    why: "This is the constructed hard case: only the shared threat memory saves victim B.",
    real: "Real: the antibody match in txn-guard. Simulated: everything else, including the miss.",
  },
  bank_b_misses: {
    what: "Bank B scored the transfer without a shared antibody and let it through.",
    why: "This is what happens without shared threat memory (for example when the analyst released the hold).",
    real: "Real: the scoring path. Simulated: the transfer.",
  },
  analyst_released: {
    what: "The analyst released the hold, so no antibody is published.",
    why: "Bank B has no shared memory of this mule and must rely on its own signals.",
    real: "Real: the release path. Simulated: the case.",
  },
  cash_out: {
    what: "Funds that were forwarded through the mule layer reach a cash-out account; the ring graph and map show the route.",
    why: "It shows where a ring monetises, and what a takedown would have to cut.",
    real: "Model of this run's own transactions, not a prediction.",
  },
  evidence_sealed: {
    what: "Every decision was appended to a hash-chained, signed ledger. A case package can be verified offline against the ledger's public key.",
    why: "A court or regulator can check the record without trusting this server.",
    real: "Real: the package and the verifier. Simulated: the case itself.",
  },
};
