#!/usr/bin/env bash
# Run every test suite in the repo and summarise pass/fail. Non-zero exit if any suite fails.
#   scripts/test_all.sh                 all python packages + web-console unit tests
#   scripts/test_all.sh services/gateway tests/e2e/bench     only the named packages
#
# Python: `python -m pytest -W error -q` per package (warnings are errors).
# Console: `npm test` (vitest) if web-console/node_modules exists; Playwright e2e is separate
# (`cd web-console && npm run test:e2e`).
#
# Known long suites (approximate, one laptop): txn_guard ~90 s, graph_twin ~55 s, demo_server ~50 s,
# bench ~60 s, call_guard ~25 s. The whole run is several minutes.
set -u
cd "$(dirname "$0")/.."
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
fi
case "$PY" in /*) ;; *) PY="$(command -v "$PY")" ;; esac
[ "${PY#.}" != "$PY" ] && PY="$PWD/$PY"

if [ "$#" -gt 0 ]; then
  PKGS=("$@")
else
  PKGS=()
  while IFS= read -r line; do PKGS+=("$line"); done < <(
    find . -name pyproject.toml -not -path "./.venv/*" -not -path "*/node_modules/*" \
      -not -path "./.git/*" | sed 's|^\./||; s|/pyproject.toml$||' | sort)
  [ -f web-console/package.json ] && PKGS+=("web-console")
fi

pass=0; fail=0; skipped=0
declare -a results
for p in "${PKGS[@]}"; do
  start=$(date +%s)
  if [ -f "$p/pyproject.toml" ]; then
    kind="pytest"
    out=$(cd "$p" && "$PY" -m pytest -W error -q 2>&1); rc=$?
  elif [ -f "$p/package.json" ]; then
    kind="vitest"
    if [ ! -d "$p/node_modules" ]; then
      results+=("SKIP  $p (no node_modules: run 'cd $p && npm ci')"); skipped=$((skipped+1)); continue
    fi
    out=$(cd "$p" && npm test --silent 2>&1); rc=$?
  else
    results+=("SKIP  $p (no pyproject.toml or package.json)"); skipped=$((skipped+1)); continue
  fi
  secs=$(( $(date +%s) - start ))
  summary=$(printf '%s\n' "$out" | grep -E "passed|failed|error|Tests " | tail -1)
  if [ $rc -eq 0 ]; then
    pass=$((pass+1)); results+=("PASS  $p [$kind, ${secs}s] $summary")
  else
    fail=$((fail+1)); results+=("FAIL  $p [$kind, ${secs}s] $summary")
    printf '%s\n' "$out" | tail -25
  fi
  echo "${results[${#results[@]}-1]}"
done

echo
echo "================ test-all summary ================"
printf '%s\n' "${results[@]}"
echo "packages: $pass passed, $fail failed, $skipped skipped"
[ "$fail" -eq 0 ]
