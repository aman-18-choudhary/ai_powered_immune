# geo-intel

A read model that turns reported complaints into city-level hotspots, trends and cash-out points
for the console's map. Standalone service behind the gateway's `/api/geo/*` prefix.

Model version: `geo-poisson-v1`. Python >= 3.11. Tests: `pytest -W error` (about 1 s; one
Postgres test is opt-in via `GEO_TEST_POSTGRES_URL`).

## Design

```
complaints ── svckit.consume (group geo-intel, DLQ on poison) ─> GeoService.ingest (to_thread)
                                                                   │ SQLAlchemy Core: gi_complaints
HTTP (FastAPI, gateway trust) ── /hotspots /points /cities ────────┘ (SQLite tests, Postgres stack)
```

* **Input: `Topics.COMPLAINTS` only.** The event is `scam_contracts.models.Complaint`:
  `complaint_id` (opaque), `ts`, `kind` (`scam_call` | `held_transfer` | `cash_out`), a `city_code`
  or `state_code` from a closed enum (`scam_contracts.geo`), optional coarse `amount_bucket` and
  opaque `case_ref`. No free text, phone number, coordinate or person identifier exists in the
  schema. Producers today: `citizen-shield` `POST /report` (city or state code chosen by the
  citizen from a fixed list). The simulator does not emit complaints yet.
* **`txn.decisions` is not consumed.** `TxnDecision` carries no location, bank or payer city, and
  inventing one would be fabricated geography. Geo points therefore come from complaints only; a
  held transfer appears on the map when somebody reports it (`kind=held_transfer`). If decisions
  gain a coarse location field later, a second consumer can be added without changing the API.
* **Resolution.** A `city_code` maps to its table row; a `state_code` alone maps to the state's
  anchor city (its largest listed city) and the point is flagged `precision: "state"`. If both are
  present the city wins. The table (`geo_intel/table.py`, 27 cities, 17 states) is the single copy:
  the demo-server imports it from here.
* **Storage.** One table, `gi_complaints`, with a unique `complaint_id`, the resolved city code,
  precision, kind, amount bucket and case ref. Coordinates are never stored; they are looked up in
  the table when serving. `python -m geo_intel.migrate` creates the schema (`GEO_CREATE_SCHEMA=0`
  for replicas afterwards).
* **Hotspots.** For a window `W` ending at `as_of`, each city's non-cash-out count in
  `(as_of - W, as_of]` is compared with a Poisson baseline: the mean per window over the `B`
  (default 6) windows before it, floored at `GEO_MIN_RATE` (0.5). `z = (count - lambda) /
  sqrt(lambda)`; a hotspot needs `z >= 3.0` and `count >= 3`. `trend` compares the last two
  windows. Cash-out points are listed separately (retained total and current-window count).
* **`as_of`.** Defaults to the newest accepted complaint time (so a replayed simulation reads as of
  its own clock, and output is a pure function of stored data); `?at=` (timezone-aware ISO-8601)
  replays an earlier moment. The response carries `as_of`, so a client can see how stale it is.

## API (all JSON; everything except the probes needs gateway trust and role officer/analyst/admin)

| Route | Notes |
|---|---|
| `GET /hotspots?window=1h&at=` | `window` is `<n>[smhd]`, 60 s to 7 d (default `GEO_WINDOW_S`); cells sorted hotspots first, then by z, count, city code; weak `ETag`, `If-None-Match` gives 304 |
| `GET /points?since=&before=&limit=` | newest first; `before` is the previous page's `next_before` cursor; `limit` 1..`GEO_MAX_LIMIT` (500, larger values are clamped, 0 is 422); items omit `complaint_id` and `case_ref` |
| `GET /cities` | the static table with codes and weights |
| `GET /healthz`, `/readyz` | liveness; readiness pings the database (503 when it fails) |
| `GET /metrics` | `GEO_METRICS_TOKEN` (`X-Metrics-Token`) or gateway trust with a reader role; counters for ingested, duplicates, dropped (old, future, location), pruned, store errors and the row gauge |

Through the gateway these are `/api/geo/hotspots`, `/api/geo/points`, `/api/geo/cities`.

## Configuration

`GEO_DATABASE_URL` (default in-memory SQLite), `GEO_CREATE_SCHEMA`, `KAFKA_BOOTSTRAP` (the consumer
runs only when set), `GEO_GATEWAY_SECRET` or `TRUST_GATEWAY_HEADERS=1`, `GEO_METRICS_TOKEN`,
`GEO_RETENTION_DAYS` (30), `GEO_WINDOW_S` (3600), `GEO_BASELINE_WINDOWS` (6), `GEO_Z_THRESHOLD`
(3.0), `GEO_MIN_COUNT` (3), `GEO_MIN_RATE` (0.5), `GEO_FUTURE_SKEW_S` (3600), `GEO_MAX_LIMIT`
(500), `GEO_MAX_BODY_BYTES` (16384), `GEO_PRUNE_INTERVAL_S`, `GEO_MAX_RETRIES`. Run:
`python -m geo_intel` (uvicorn, `access_log=False`).

## Guarantees and their limits

* **Idempotent ingestion.** The unique `complaint_id` makes redelivery a no-op; for a repeated id
  with different content the first writer wins (the later one is counted as a duplicate).
* **Out-of-order tolerant.** Events are stored by event time; arrival order does not change any
  aggregate (tested). An event older than the retention window behind the newest accepted one, or
  more than `GEO_FUTURE_SKEW_S` ahead of the service clock, is dropped and counted, never retried.
* **Bounded storage.** Rows older than the retention window (relative to the newest accepted
  event) are pruned in batches; reads aggregate in SQL, so response size is bounded by the number
  of table cities (27) and the page limit.
* **Failure behaviour.** A database failure while ingesting raises, so `svckit.consume` retries
  and then dead-letters the message (`complaints.dlq`); reads answer a generic 503. Every
  database call runs in a worker thread; the lifespan cancels the tasks and disposes the engine.
* **No audit entries.** This is a derived read model; complaints that matter as evidence belong to
  the ledger path of the service that accepted them.

## Threat model

* **Trust.** Only the gateway reaches the service. Principal headers are honoured when
  `GEO_GATEWAY_SECRET` matches `X-Gateway-Secret` (constant-time byte comparison; non-ASCII input
  fails the comparison and answers 401) or when `TRUST_GATEWAY_HEADERS=1`. Without either, every
  data route answers 401.
* **Input.** Query parameters are validated (422 with location/message/type only, never the
  submitted value), request bodies are capped (413), request ids are replaced unless 8-64 chars of
  `[A-Za-z0-9-]`, and access logs carry method, route template, status, request id and duration
  only: no query string, header or body.
* **Poisoning.** A producer on the bus can inflate a city's count; hotspots are a statistical
  signal, not proof, and the producer must be authenticated at the bus. A single actor spamming a
  city is bounded only by `complaint_id` uniqueness and the retention window; there is no
  per-producer rate limit here (citizen-shield rate-limits `/report`).
* **Privacy.** Nothing finer than a city is stored or served; points omit complaint and case
  identifiers. Small counts in a small city can still single out an event; the API does not apply
  k-anonymity thresholds.

## Honest limits

* City-level only, on a simulated geography: coordinates are approximate city centres from a
  static 27-row table, not real geolocation data; no telecom, device or IP location is used.
* The z-score is a normal approximation to a Poisson count and is rough at low rates (hence
  `min_count` and `min_rate`); thresholds are untuned defaults, not validated on real data.
* A state-only report is shown at the state's anchor city, which over-concentrates those
  reports on one city by design (flagged with `precision: "state"`).
* Not load-tested; one replica is assumed (pruning and the watermark cache are per process, and
  correctness across replicas rests on the unique key only).
