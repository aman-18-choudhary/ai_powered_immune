#!/usr/bin/env bash
# Start a disposable local Postgres on a high port, run the opt-in Postgres tests, stop it.
# Needs initdb / pg_ctl on PATH (e.g. Homebrew postgresql) and psycopg2 in the venv.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${LEDGER_TEST_PG_PORT:-54331}"
for d in /opt/homebrew/opt/postgresql*/bin /usr/lib/postgresql/*/bin; do
  [ -d "$d" ] && PATH="$d:$PATH"
done
command -v initdb >/dev/null || { echo "initdb not found" >&2; exit 2; }
DIR="$(mktemp -d)"
cleanup() { pg_ctl -D "$DIR/data" -m immediate stop >/dev/null 2>&1 || true; rm -rf "$DIR"; }
trap cleanup EXIT
initdb -D "$DIR/data" -A trust -U ledger >/dev/null
pg_ctl -D "$DIR/data" -o "-p $PORT -k $DIR -c listen_addresses=127.0.0.1" -l "$DIR/log" -w start >/dev/null
createdb -h 127.0.0.1 -p "$PORT" -U ledger ledgertest
export LEDGER_TEST_POSTGRES_URL="postgresql+psycopg2://ledger@127.0.0.1:$PORT/ledgertest"
"${PYTHON:-python}" -m pytest -W error -q tests/test_postgres.py "$@"
