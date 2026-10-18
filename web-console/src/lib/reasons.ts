/**
 * Human-readable meanings for every reason code the scorers emit (txn-guard, call-guard and the
 * citizen assistant). Unknown codes fall back to a humanised label so a new server-side code
 * never breaks the UI. A unit test checks this table against the recorded fixture and, where
 * greppable, the services' own sources.
 */
export interface ReasonInfo {
  label: string;
  /** One line, plain English, what the code means for the person reading it. */
  meaning: string;
  source: "txn-guard" | "call-guard" | "antibody" | "policy";
}

export const REASONS: Record<string, ReasonInfo> = {
  // txn-guard: feature-derived
  ACTIVE_SCAM_CALL: { label: "Active scam call", meaning: "The payer was on a call flagged as a scam in the last 15 minutes.", source: "txn-guard" },
  YOUNG_PAYEE_ACCOUNT: { label: "Young payee account", meaning: "The receiving account is only days old, a common mule trait.", source: "txn-guard" },
  AMOUNT_ANOMALY: { label: "Unusual amount", meaning: "The amount is far above this payer's typical transfer.", source: "txn-guard" },
  VELOCITY_SPIKE: { label: "Velocity spike", meaning: "Several transfers in the last hour, faster than this payer's normal.", source: "txn-guard" },
  NEW_PAYEE: { label: "New payee", meaning: "First payment from this payer to this receiver.", source: "txn-guard" },
  NEW_DEVICE: { label: "New device", meaning: "The payer used a device not seen before on this account.", source: "txn-guard" },
  NIGHT_TRANSFER: { label: "Night transfer", meaning: "Sent at night, when scams and account takeovers cluster.", source: "txn-guard" },
  NO_RISK_INDICATORS: { label: "No risk cues", meaning: "No scam cues fired; not a guarantee of safety.", source: "txn-guard" },
  // txn-guard: policy overlays (floors that lift the model score)
  CALL_RISK_AMOUNT_GUARD: { label: "Call-risk amount guard", meaning: "An active scam call plus a large unusual amount to a new payee forces a hold.", source: "policy" },
  YOUNG_PAYEE_LARGE_AMOUNT_FLOOR: { label: "Young payee, large amount", meaning: "A large transfer to a very new account is held regardless of the model score.", source: "policy" },
  PAYEE_AMOUNT_ESCALATION: { label: "Payee amount escalation", meaning: "Amounts to this payee are growing sharply, a test-then-escalate pattern.", source: "policy" },
  NEW_PAYEE_EXTREME_AMOUNT: { label: "New payee, extreme amount", meaning: "An extreme amount to a payee this payer has never used.", source: "policy" },
  FUTURE_DATED_TIMESTAMP: { label: "Future-dated timestamp", meaning: "The event claims a time in the future; treated as suspicious input.", source: "policy" },
  RAIL_TYPICAL_AMOUNT_DAMPER: { label: "Rail amount damper", meaning: "Score capped because the amount is typical for this payment rail (for example NEFT).", source: "policy" },
  // antibody threat memory
  ANTIBODY_MATCH: { label: "Antibody match", meaning: "Another bank confirmed this receiver as a mule; shared threat memory fired.", source: "antibody" },
  ANTIBODY_MATCH_KNOWN_PAYEE: { label: "Antibody match (known payee)", meaning: "A confirmed-mule antibody matches a payee this payer has paid before.", source: "antibody" },
  LATE_ANTIBODY_POST_SETTLEMENT: { label: "Late antibody", meaning: "An antibody arrived after this payment was allowed; the hold is a recall/verify request.", source: "antibody" },
  LATE_CALL_RISK_POST_SETTLEMENT: { label: "Late call risk", meaning: "Scam-call risk arrived after this payment was allowed; the hold is a recall/verify request.", source: "txn-guard" },
  // call-guard
  DIGITAL_ARREST_PHRASE: { label: "Digital-arrest phrase", meaning: "The caller says the victim is under 'digital arrest'.", source: "call-guard" },
  SAFE_ACCOUNT_TRANSFER: { label: "Safe-account demand", meaning: "The caller wants funds moved to a 'safe', RBI or verification account.", source: "call-guard" },
  ISOLATION_DEMAND: { label: "Isolation demand", meaning: "The caller demands the victim stay on the call, stay silent or stay on camera.", source: "call-guard" },
  PAYMENT_DEMAND: { label: "Payment demand", meaning: "The caller demands money in connection with an arrest or case.", source: "call-guard" },
  AUTHORITY_IMPERSONATION: { label: "Authority impersonation", meaning: "The caller claims to be police, CBI, a court or a regulator.", source: "call-guard" },
  LEGAL_THREAT: { label: "Legal threat", meaning: "The caller alleges a criminal case, warrant or seized parcel.", source: "call-guard" },
  ACCOUNT_VERIFICATION_ASK: { label: "Account verification ask", meaning: "The caller asks the victim to 'verify' an account, balance or ID.", source: "call-guard" },
  CREDENTIAL_ASK: { label: "Credential ask", meaning: "The caller asks for an OTP, PIN or password.", source: "call-guard" },
  URGENCY: { label: "Time pressure", meaning: "The caller applies pressure to act immediately.", source: "call-guard" },
  SCRIPT_CLASSIFIER_MATCH: { label: "Known scam script", meaning: "The language model recognises the wording of a known digital-arrest script.", source: "call-guard" },
  // rules-v2 (accuracy batch 2, 2026-10-18)
  UPI_PIN_TO_RECEIVE: { label: "PIN to receive money", meaning: "Asks for a UPI PIN or a request approval in order to receive money; receiving never needs a PIN.", source: "call-guard" },
  ADVANCE_FEE_DEMAND: { label: "Advance fee", meaning: "Asks for a fee or deposit to start a job or task, or to release earnings.", source: "call-guard" },
  GUARANTEED_RETURNS: { label: "Guaranteed returns", meaning: "Promises unrealistic guaranteed returns or doubling of money.", source: "call-guard" },
  SEXTORTION_THREAT: { label: "Sextortion threat", meaning: "Threatens to spread private images or recordings unless paid.", source: "call-guard" },
  ACCOUNT_BLOCK_THREAT: { label: "Account block threat", meaning: "Threatens to block an account, card or SIM unless KYC is updated or a link is used.", source: "call-guard" },
  REPORTED_SCAM_ENCOUNTER: { label: "Reported scam encounter", meaning: "Describes money or details taken through a known scam method.", source: "call-guard" },
};

export function humanise(code: string): string {
  const s = code.toLowerCase().replace(/[_\s]+/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "Unknown";
}

export function isKnownReason(code: string): boolean {
  return Object.prototype.hasOwnProperty.call(REASONS, code);
}

export function reasonInfo(code: string): ReasonInfo & { known: boolean } {
  if (isKnownReason(code)) return { ...(REASONS[code] as ReasonInfo), known: true };
  return { label: humanise(code), meaning: "A reason code this console does not describe yet; shown as received.", source: "txn-guard", known: false };
}
