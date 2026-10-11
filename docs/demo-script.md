# Demo script (3 minutes)

A narrated walkthrough synchronised to the console's cinematic pacing. Everything on screen is
simulated, and the script says so. A silent (no-audio) screen recording of the hero run exists:
[demo/hero-run.webm](demo/hero-run.webm) (2:49, 1440x900; also mp4, a 45 s GIF and a caption
track, see [demo/README.md](demo/README.md)). This script is the voice-over for it, or for a
live or re-recorded take; the checklist at the end is for a re-take.

## Setup (before recording)

```
make install        # once
make demo           # builds the console, serves http://localhost:8080
```

Open `http://localhost:8080`, pick the **Officer** role on the splash (Officer or Admin can run
the scenario). The console starts seed 1 with `pacing: "cinematic"` at 1x, which is what the
timings below describe. Leave the **SIMULATION** pill visible.

## Timings (measured)

Offsets are `wall_offset` of the `scenario.step` events at 1x (seconds after you press Play),
read from `/api/demo/state` and the stream snapshot for each seed. A real-time run of seed 1
through the HTTP server finished in 72.9 s (timeline total 71.14 s; the extra is polling and
start-up). They are deterministic per seed; a human pressing Confirm earlier shifts the later
steps.

| Step key | Console label | Seed 1 | Seed 2 | Seed 3 |
|---|---|---|---|---|
| `call_begins` | A scam call begins | 0.0 | 0.0 | 0.0 |
| `call_flagged` | Call-guard flags the call | 0.0 | 1.9 | 1.3 |
| `transfer_held` | Bank A holds the transfer | 24.7 | 18.6 | 19.1 |
| `analyst_confirms` | Analyst confirms the mule | 27.2 | 20.4 | 21.2 |
| `antibody_shared` | Antibody shared with other banks | 27.2 | 20.4 | 21.2 |
| `bank_b_blocks` | Bank B blocks the same mule | 36.7 | 25.0 | 39.1 |
| `cash_out` | Funds reach the cash-out account | 51.2 | 35.9 | 37.9 |
| `evidence_sealed` | Evidence sealed | 71.1 | 61.0 | 69.3 |
| run total (`wall_duration`) | | 71.14 | 60.99 | 69.27 |

Use **seed 1**: it is the console default, and its step order is the one the narration follows.
In seed 3 the cash-out lands 1.2 s before Bank B's block; the other seeds are reachable only via
`POST /api/demo/start`.

## Script

Clock = minutes:seconds in the recording; "run +N" = seconds after Play.

| Clock | Run | On screen | Say |
|---|---|---|---|
| 0:00 | | Splash, then the Live page, idle. Point at the SIMULATION pill | "Digital-arrest scams move money in minutes, and every bank sees only its own customers. This is a platform that makes one bank's detection protect all of them. Everything you will see is simulated: simulator calls, simulator banks, the real detection code." |
| 0:15 | | Hover **About these numbers** briefly | "The hero is a constructed hard case. The mule is seasoned, so no young-account signal fires, and the second victim's call is deliberately off-script, so call-guard misses it. Only shared memory can save her." |
| 0:25 | +0 | Press **Play** (or Space). Phone pane shows the call | "Victim A, at bank A, gets a call from a fake investigator. Call-guard scores each line as it arrives." |
| 0:25 | +0 | Timeline chip **Flagged** | "The session score crosses 0.7 from a known-script match plus legal-threat and authority cues. A call alone never moves or blocks money; it raises the risk the transaction scorer uses." |
| 0:50 | +24.7 | Bank A pane: transfer held, countdown ring | "Victim A starts a transfer to a new payee. The amount is far above her norm, the payee is new and a scam call is active, so bank A holds it for verification. The money does not move while the hold is open." |
| 0:52 | +27.2 | **Confirmed** then **Shared**; antibody ticker | "An analyst confirms the payee is a mule. That label becomes an antibody: a keyed hash of the account, not a name or number. It is published and applied at the other banks. The latency you see is measured in this process." |
| 1:02 | +36.7 | Bank B pane: **B blocks**, reason `ANTIBODY_MATCH` | "Ninety seconds later victim B, at another bank in another state, pays the same mule. Her call was not flagged and the account looks fine locally. The antibody match floors the decision to hold. Nothing local to bank B would have caught this." |
| 1:16 | +51.2 | Map: cash-out arcs | "The ring graph and map show where the money would have gone: mule layer to a cash-out account." |
| 1:36 | +71.1 | Finale card: evidence sealed | "Every decision was appended to a hash-chained, signed ledger." |
| 1:40 | | Click **Verify offline** | "The package ships its own verifier. It runs here in an isolated interpreter, pinned to the ledger's public key." |
| 1:55 | | Verify result: `PASS_PINNED` | "Chain, checkpoint and manifest signatures all check. A court expert can run the same script with nothing installed. We do not claim legal admissibility; that is for counsel." |
| 2:05 | | `/command`: KPIs, map, ring panel | "The command centre sees the same case as a map and a ring." |
| 2:15 | | Ring panel: drag **Freeze k accounts** to 2, or press **Best takedown** | "Freeze two accounts and the model projects the harm reduction. It is a what-if on this run's own transactions, not a prediction." |
| 2:30 | | `/insights`: headline table, rail table, ablations | "Benchmark on simulator data, with confidence intervals. Antibody sharing lifts victim-transfer recall from 94.5% to 100% in this simulator. We show NEFT separately because the simulator has no NEFT scams: its false-positive figure is a benign-traffic result only." |
| 2:45 | | `/citizen`: tap a sample, verdict card | "And the citizen side: paste a message, get one verdict in plain language, in English, Hindi, Tamil or Bengali. 'Safe' only ever means no clear scam cues found." |
| 2:55 | | Back to Live finale | "One detection, every bank protected, every action auditable. Code, benchmark and the audit trail are in the repository." |

## If the stream drops

The console shows the connection state and raises a toast on loss and reconnect. The WebSocket
client reconnects with backoff, and the server's first message after every connect is a fresh
`scenario.state` snapshot (counters plus the last 200 events), so the page resynchronises without
a restart.

1. Wait up to about 8 s (reconnect backoff is 0.5 s doubling to 8 s). Do not click Restart.
2. If the page is stuck, reload: the run continues server-side and the snapshot restores the
   timeline.
3. If the run was lost (server restarted), press Play again; the same seed produces the same
   events. Cut to the step you were on.
4. Last resort: `docs/screenshots/player-finale-1440x900-dark.png` and
   `docs/screenshots/evidence-verified-1440x900-dark.png` show the end state.
5. Offline check without the console: `make verify-package` prints the pinned verification of the
   same package from the terminal.

## Q&A cheat-sheet

| Likely question | Honest answer |
|---|---|
| Is this real data? | No. Calls, customers, banks and mules come from a seeded simulator; models were trained on disjoint simulator seeds. Every REST body carries `"simulated": true`. |
| Are the numbers real-world performance? | No. Precision 100% and recall 82.6% at seed 42 are simulator numbers from the same generators the models were trained on. The report says to read them as a regression and ablation harness. |
| How can precision be 100%? | Zero benign transactions were held in 21,736. The 95% interval upper bound on the false-positive rate is 0.018%. Real prevalence is lower than the simulator's 3.03%, and precision depends on prevalence (see the prevalence table). |
| What about the call-guard result? | It alerts on all 163 scam calls and none of 1,076 benign ones, which is idealised. The `degraded_call_signal` ablation uses a training-like imperfect signal (recall 82.2%, precision 99.8%). |
| Is the hero cherry-picked? | It is constructed on purpose: a seasoned mule and an evasive call. Across 40 hero seeds the model alone allowed B's first transfer 39 times; with the antibody all 40 were held. The 40 seeds vary mule age, amounts and timing, not the script. |
| Does it work for NEFT? | There are no NEFT scams in the simulator. NEFT thresholds are scaled by hand (about 5x) and tested on hand-written scenarios; NEFT benign hold is 0.00% and flagged 0.83% in the benchmark. |
| Why not block outright? | Hold-and-verify keeps a human or the citizen in the loop; holds never auto-release or auto-block past the deadline, they are flagged overdue. |
| What if an antibody is wrong? | 14-day TTL, revoke tombstones that reach banks in seconds, a protected-merchant allowlist, and established payees are stepped up rather than held. Antibody poisoning and false confirmations are not simulated. |
| What does the hub learn? | Only keyed hashes. The federation key stays with the banks; the hub cannot brute-force the account space. The hash is linkable by anyone with that key. |
| Is the evidence admissible? | We make no such claim. The package is tamper-evident and offline-verifiable; the Section 63 certificate is a template for the operator and counsel. |
| Does the ledger prove the decision was right? | No. Integrity is not truthfulness: it proves what services recorded, in what order, unaltered. |
| Is it running as microservices? | The Docker stack exists (`infra/docker-compose.yml`) and validates with `docker compose config`, but it has not been run. The demo is a single process running the same service code over an in-memory bus. |
| Why not Neo4j / LightGBM? | No Docker daemon to test Neo4j, so graph-twin persists through SQLAlchemy; scikit-learn gradient boosting avoids a native libomp dependency. Both are in the decisions log (`docs/architecture.md`). |
| Latency? | Measured consumer-path latency with fakeredis stores is p50 about 7 ms and p99 about 14 ms per transaction (txn-guard README). It has not been load-tested against real Kafka and Redis. |
| Can it scale? | Designed to: payer-keyed partitions, stateless scorers over Redis, replicated hub and ledger. Not load-tested here; graph-twin is a single replica. |

## Recording checklist

- [ ] Resolution 1920x1080 (browser window 1440x900 at 100% zoom also works and matches the
      screenshots); hide bookmarks and extensions.
- [ ] Dark theme (default) for the main run; optionally re-shoot the finale in light.
- [ ] Speed 1x for the narrated take (2x only for a cut-down trailer: halve every run offset).
- [ ] `make demo` freshly started; officer role; SIMULATION pill visible in frame.
- [ ] Do one dry run first: a second Play of the same seed replays identically.
- [ ] Notifications off, no other windows, microphone level checked, cursor visible.
- [ ] Record with the OS screen recorder (macOS: Cmd+Shift+5, record selected portion; Windows:
      Game Bar Win+G; Linux: OBS). Record audio separately if you plan to re-time the voice-over.
- [ ] Target 3:00; the run itself is 71 s, the rest is walkthrough. Keep the "constructed hard
      case" sentence and the "simulated" sentence in the final cut.
- [ ] Export 1080p H.264, check the verify PASS frame is legible.

Known gap: the existing recording has no audio narration; a voice-over still has to be read over it
using the script above.
