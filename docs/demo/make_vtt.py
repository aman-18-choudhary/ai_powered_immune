"""Build hero-run.vtt from hero-run.marks.json (written by web-console/e2e/record-hero.mjs) and the
seed-1 step offsets measured in docs/demo-script.md. Run: python3 make_vtt.py"""

import json
import pathlib

here = pathlib.Path(__file__).parent
m = json.loads((here / "hero-run.marks.json").read_text())
p = m["play"]
STEP = {
    "call": 0.0,
    "flagged": 0.0,
    "held": 24.7,
    "confirmed": 27.2,
    "b": 36.7,
    "cash": 51.2,
    "sealed": 71.1,
}


def ts(s):
    h, r = divmod(s, 3600)
    mi, sec = divmod(r, 60)
    return f"{int(h):02d}:{int(mi):02d}:{sec:06.3f}"


cues = [
    (0.0, m["splash"], "ScamShield Immune. Everything on screen is simulated data."),
    (
        m["splash"],
        m["play"],
        "Live page, idle. The SIMULATION pill stays visible. The hero is a constructed hard case.",
    ),
    (
        p,
        p + 24.0,
        "Victim A is phoned by a fake investigator. Call-guard scores each line and flags the call. A call alone never moves or blocks money.",
    ),
    (
        p + STEP["held"],
        p + STEP["confirmed"],
        "Bank A holds the transfer to a new payee while the scam call is active.",
    ),
    (
        p + STEP["confirmed"],
        p + STEP["b"],
        "An analyst confirms the mule. The antibody is a keyed hash, shared with the other banks.",
    ),
    (
        p + STEP["b"],
        p + STEP["cash"],
        "Victim B, another bank, call not flagged: the antibody match holds the transfer.",
    ),
    (
        p + STEP["cash"],
        p + STEP["sealed"],
        "The ring graph and map show the cash-out. Every decision is appended to the ledger.",
    ),
    (
        m["finale"],
        m["verify"],
        "Evidence sealed. The package verifies offline with its own verifier.",
    ),
    (
        m["verify"],
        m["command"],
        "PASS_PINNED: chain, checkpoint and manifest signatures check. No admissibility claim is made.",
    ),
    (m["command"], m["analyst"], "Command centre: KPIs, map and ring panel."),
    (m["analyst"], m["evidence"], "Analyst console: hold queue sorted by deadline."),
    (
        m["evidence"],
        m["citizen"],
        "Evidence: hash chain, signed checkpoints, package export and offline verification.",
    ),
    (
        m["citizen"],
        m["citizen_ta"],
        "Citizen shield: paste a message, get one plain-language verdict.",
    ),
    (
        m["citizen_ta"],
        m["insights"],
        "The same verdict in Tamil. Four languages are supported.",
    ),
    (
        m["insights"],
        m["outro"],
        "Insights: benchmark on simulator data with confidence intervals, per-rail false positives, ablations.",
    ),
    (m["outro"], m["end"], "Run it yourself: make demo."),
]
out = ["WEBVTT", ""]
for i, (a, b, t) in enumerate(cues, 1):
    out += [str(i), f"{ts(a)} --> {ts(b)}", t, ""]
(here / "hero-run.vtt").write_text("\n".join(out))
print(len(cues), "cues")
