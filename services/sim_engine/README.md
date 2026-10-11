# sim-engine

Seeded synthetic ecosystem with ground-truth labels. It is the only data source in this
repository: every call, customer, account and transaction comes from here.

* `world.py`: citizens, banks (`bank_sbi`, `bank_hdfc`, ...), accounts and cities. Raw
  identifiers live only here; everything leaving the world is tokenised (`svckit.Tokenizer`) or
  keyed-hashed (`scam_contracts.hashing.keyed_hash`).
* `benign.py`: benign payment traffic (UPI, IMPS, NEFT).
* `calls.py`, `scam.py`: scripted call transcripts (`en`, `hi`, `hi-Latn`) and digital-arrest
  campaigns: call, victim transfers, mule fan-in, forwards, cash-out. Scam flows use UPI and IMPS
  only: there are **no NEFT scams** in the simulator.
* `labels.py`: ground truth over generated campaigns (anything not listed is benign).
* `replay.py`: `build_hero_scenario` (a constructed hard case: seasoned mule plus an off-script
  call for victim B) and `replay`, which publishes events to a bus in time order. Events are keyed
  by payer/victim token (see `scam_contracts.topics`).
* `api.py`: `POST /scenarios/hero` (202; one run at a time, 409 while active; use a new seed to
  repeat, the same seed re-emits identical idempotency keys), `/healthz`, `/readyz`.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `KAFKA_BOOTSTRAP` | `kafka:9092` | broker |
| `SIM_SEED` | 1 | world and scenario seed |
| `SIM_SPEED` | 60 | simulated seconds per wall second |
| `SIM_CITIZENS` | 2000 | world size |
| `SIM_BANKS` | 4 | number of banks (1..8); ids are positional (`bank_sbi`, `bank_hdfc`, `bank_icici`, ...); the compose stack sets 2 to match its two txn-guard `TXN_BANK_ID`s |
| `PORT` | 8000 | listen port |

## Test

```
cd services/sim_engine && python -m pytest -W error -q
```

All data is simulated; nothing here is a statement about real fraud.
