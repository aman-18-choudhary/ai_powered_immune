# gateway

Edge service for the multi-container stack: login, JWT verification, role-based access,
per-role rate limiting and a reverse proxy to the internal services. (The single-process
`demo-server` has its own, simpler demo login and does not use this service.)

## Endpoints

| Path | Notes |
|---|---|
| `POST /auth/login` `{username, password}` | `{access_token, token_type, expires_in}`; HS256, 30 min; failed-login limiter per (IP, username) |
| `GET /healthz`, `GET /readyz` | liveness / readiness |
| `/api/txn/**` | roles analyst, officer, admin -> `GATEWAY_UPSTREAM_TXN` |
| `/api/graph/**`, `/api/geo/**` | roles officer, analyst, admin -> `GATEWAY_UPSTREAM_GRAPH` / `_GEO` |
| `/api/ledger/**` | roles officer, admin -> `GATEWAY_UPSTREAM_LEDGER` |
| `/api/citizen/**` | roles citizen, admin -> `GATEWAY_UPSTREAM_CITIZEN` |

`/api/<prefix>/<path>` is forwarded to `<upstream>/<path>` with `X-Principal-Role`,
`X-Principal-Sub` and `X-Request-Id` set by the gateway (client-supplied values are stripped).
An unset upstream answers `502 upstream_not_configured`. Request bodies are capped
(`GATEWAY_MAX_BODY_BYTES`, 1 MiB), responses too (`GATEWAY_MAX_RESPONSE_BYTES`, 5 MiB); `.`, `..`,
NUL and backslash path segments are rejected.

The gateway does **not** add a per-service shared secret. Upstreams must therefore be reachable
only from the gateway (internal network) and run with `TRUST_GATEWAY_HEADERS=1`, as in
`infra/docker-compose.yml`. It does not tokenise identifiers.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `GATEWAY_JWT_SECRET` | required | HS256 secret, at least 32 bytes (startup fails otherwise) |
| `GATEWAY_DEMO_USERS` | unset | `1` creates one demo account per role (username = role). There is no other user store. Never in production |
| `GATEWAY_DEMO_PASSWORD[_<ROLE>]` | random, logged once | demo passwords |
| `GATEWAY_REDIS_URL` | `redis://redis:6379/0` | rate-limit state; the limiter fails open |
| `GATEWAY_UPSTREAM_{TXN,GRAPH,GEO,LEDGER,CITIZEN}` | unset | upstream base URLs |
| `PORT` | 8000 | listen port |
| `FORWARDED_ALLOW_IPS` | uvicorn default | set to the proxy address when behind one, so login limiting sees client IPs |

Rate limits per role per minute: citizen 30, analyst 300, officer 300, admin 600.

## Known trade-offs

* A per-username failed-login key lets an attacker lock a username out for up to 60 s per window
  in exchange for stopping credential stuffing.
* Rate limiting fails open when Redis is down.

## Run and test

```
python -m gateway                      # needs GATEWAY_JWT_SECRET
cd services/gateway && python -m pytest -W error -q
```
