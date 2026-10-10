import type { VerifyResult, VerifyStatus } from "@/types/api";

export interface VerifyView {
  status: VerifyStatus;
  tone: "safe" | "warn" | "danger";
  title: string;
  summary: string;
  exit: { code: number; meaning: string };
  /** Lines of the verifier's output with their pass/info/fail classification. */
  lines: { text: string; kind: "pass" | "info" | "fail" }[];
}

const EXIT_MEANING: Record<number, string> = {
  0: "Exit 0: every check passed and the signature is from the pinned ledger key.",
  1: "Exit 1: a check failed (tampered, truncated or mis-signed package).",
  2: "Exit 2: the package is internally consistent but no public key was pinned, so who signed it is not established.",
};

export function exitMeaning(code: number): string {
  return EXIT_MEANING[code] ?? `Exit ${code}: unexpected exit status; treated as a failure.`;
}

/** Map the verifier result to what the UI shows. Anything unrecognised is a FAIL (fail closed). */
export function verifyView(r: VerifyResult): VerifyView {
  const known: VerifyStatus[] = ["PASS_PINNED", "INTEGRITY_OK_UNPINNED", "FAIL"];
  const status: VerifyStatus = known.includes(r.status) ? r.status : "FAIL";
  const coherent =
    (status === "PASS_PINNED" && r.exit_code === 0) ||
    (status === "INTEGRITY_OK_UNPINNED" && r.exit_code === 2) ||
    status === "FAIL";
  const eff: VerifyStatus = coherent ? status : "FAIL";
  const base = {
    exit: { code: r.exit_code, meaning: exitMeaning(r.exit_code) },
    lines: r.lines.map((text) => ({
      text,
      kind: /^PASS\b/.test(text) ? ("pass" as const) : /^(FAIL|ERROR)\b|mismatch|invalid|not signed/i.test(text) ? ("fail" as const) : ("info" as const),
    })),
  };
  if (eff === "PASS_PINNED")
    return {
      ...base, status: eff, tone: "safe", title: "PASS: pinned",
      summary: "The package is internally consistent and signed by the ledger's pinned key. The chain binds every entry, including those whose payload is withheld.",
    };
  if (eff === "INTEGRITY_OK_UNPINNED")
    return {
      ...base, status: eff, tone: "warn", title: "Integrity OK, key not pinned",
      summary: "The hash chain is consistent, but no trusted public key was supplied, so this does not prove who produced it.",
    };
  return {
    ...base, status: "FAIL", tone: "danger", title: "FAIL",
    summary: "Verification failed. Do not rely on this package: it may be tampered with, truncated or signed by another key.",
  };
}
