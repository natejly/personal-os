#!/usr/bin/env bash
# Runs the whole test suite across many cores: backend test files in N concurrent pytest shards, Playwright specs in K
# concurrent shards, typecheck and unit tests beside the backend. Prints one combined summary.
#
# Usage: scripts/test-parallel.sh [--backend] [--e2e] [--frontend] [--jobs N] [--build]
#   no suite flag   run all three
#   --jobs N        backend shards = N; e2e total workers = N (spread over shards of TEST_E2E_WORKERS each)
#   --build         run `npm run build` first (e2e needs the built app in out/)
# Env: TEST_BACKEND_JOBS, TEST_E2E_SHARDS, TEST_E2E_WORKERS override the defaults below.
# Logs and reports land in out/test-parallel/; per-file backend durations are cached in out/test-times.json so the
# next run balances its shards by them. Scratch dirs live under $TMPDIR (or out/) and go away when the run ends.
set -uo pipefail
set -m  # each background job gets its own process group, so Ctrl-C can take down a shard with everything it spawned
cd "$(dirname "$0")/.."
ROOT="$(pwd -P)"

BACKEND_JOBS_DEFAULT=8
E2E_SHARDS_DEFAULT=4
E2E_WORKERS_DEFAULT=2

DO_BACKEND=0; DO_E2E=0; DO_FRONTEND=0; BUILD=0; JOBS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --backend) DO_BACKEND=1 ;;
    --e2e) DO_E2E=1 ;;
    --frontend) DO_FRONTEND=1 ;;
    --build) BUILD=1 ;;
    --jobs) JOBS="${2:?--jobs needs a number}"; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done
[ $((DO_BACKEND + DO_E2E + DO_FRONTEND)) -eq 0 ] && DO_BACKEND=1 DO_E2E=1 DO_FRONTEND=1

PY="$ROOT/backend/.venv/bin/python"
TOOLS="$PY $ROOT/scripts/test-parallel-tools.py"
[ -x "$PY" ] || { echo "no backend/.venv (see AGENTS.md)" >&2; exit 2; }
CORES="$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)"

B_JOBS="${JOBS:-${TEST_BACKEND_JOBS:-$BACKEND_JOBS_DEFAULT}}"
E_WORKERS="${TEST_E2E_WORKERS:-$E2E_WORKERS_DEFAULT}"
if [ -n "$JOBS" ]; then E_SHARDS=$(( (JOBS + E_WORKERS - 1) / E_WORKERS )); else E_SHARDS="${TEST_E2E_SHARDS:-$E2E_SHARDS_DEFAULT}"; fi

OUT="$ROOT/out/test-parallel"
rm -rf "$OUT"; mkdir -p "$OUT"
BASE_TMP="${TMPDIR:-$ROOT/out}"; mkdir -p "$BASE_TMP"
RUN="$(mktemp -d "$BASE_TMP/test-parallel.XXXXXX")"
STATUS=0
PIDS=()
START=$SECONDS

sweep() {  # anything still running out of this checkout
  pkill -TERM -f "$ROOT/(backend|tests/e2e|node_modules)" 2>/dev/null || true
}
cleanup() {
  for p in ${PIDS[@]+"${PIDS[@]}"}; do kill -TERM -- "-$p" 2>/dev/null || true; done
  [ -n "${MEMPID:-}" ] && kill "$MEMPID" 2>/dev/null
  sleep 1; sweep; sleep 1
  pkill -KILL -f "$ROOT/(backend|tests/e2e|node_modules)" 2>/dev/null || true
  rm -rf "$RUN"
}
trap 'echo; echo "interrupted"; cleanup; exit 130' INT TERM
trap cleanup EXIT

# Memory: sample used memory every 2 s, report the peak at the end.
mem_used_mb() {
  if [ -r /proc/meminfo ]; then awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}END{print int((t-a)/1024)}' /proc/meminfo
  else echo 0; fi
}
( while :; do mem_used_mb >> "$OUT/mem.log"; sleep 2; done ) & MEMPID=$!
disown "$MEMPID" 2>/dev/null || true

if [ "$BUILD" = 1 ]; then
  echo "» npm run build"
  npm run build > "$OUT/build.log" 2>&1 || { echo "build failed, see $OUT/build.log"; exit 1; }
fi

# ---- backend shards + frontend, concurrently ----
BACKEND_PIDS=(); FRONT_PIDS=()
if [ "$DO_BACKEND" = 1 ]; then
  $TOOLS shard "$B_JOBS" "$ROOT/out/test-times.json" "$RUN/shard-" || exit 1
  for i in $(seq 1 "$B_JOBS"); do
    mkdir -p "$RUN/tmp-b$i"
    ( cd backend && TMPDIR="$RUN/tmp-b$i" PYTHONPATH=. "$PY" -m pytest -q -p no:cacheprovider \
        --junitxml="$OUT/backend-shard-$i.xml" $(cat "$RUN/shard-$i") > "$OUT/backend-shard-$i.log" 2>&1
      echo $? > "$OUT/backend-shard-$i.code" ) &
    PIDS+=($!); BACKEND_PIDS+=($!)
  done
fi
if [ "$DO_FRONTEND" = 1 ]; then
  ( npm run typecheck > "$OUT/typecheck.log" 2>&1; echo $? > "$OUT/typecheck.code" ) & PIDS+=($!); FRONT_PIDS+=($!)
  ( npm test > "$OUT/unit.log" 2>&1; echo $? > "$OUT/unit.code" ) & PIDS+=($!); FRONT_PIDS+=($!)
fi
for p in ${BACKEND_PIDS[@]+"${BACKEND_PIDS[@]}"} ${FRONT_PIDS[@]+"${FRONT_PIDS[@]}"}; do wait "$p" 2>/dev/null; done
T_BACKEND=$((SECONDS - START))

# ---- e2e ----
E2E_SUMMARY=""; T_E2E_START=$SECONDS
if [ "$DO_E2E" = 1 ]; then
  if [ ! -f out/main/index.js ]; then echo "e2e needs a build: rerun with --build" >&2; STATUS=1
  else
    PW=(node "$ROOT/tests/e2e/node_modules/.bin/playwright" test -c "$ROOT/tests/e2e/playwright.config.mjs")
    EP=()
    for i in $(seq 1 "$E_SHARDS"); do
      mkdir -p "$RUN/tmp-e$i" "$RUN/scratch-e$i"
      ( TMPDIR="$RUN/tmp-e$i" E2E_SCRATCH="$RUN/scratch-e$i" E2E_WORKERS="$E_WORKERS" \
          E2E_REPORT="$OUT/e2e-$i.json" E2E_OUT="$RUN/e2e-out-$i" \
          "${PW[@]}" --shard="$i/$E_SHARDS" > "$OUT/e2e-shard-$i.log" 2>&1
        echo $? > "$OUT/e2e-shard-$i.code" ) &
      PIDS+=($!); EP+=($!)
    done
    for p in "${EP[@]}"; do wait "$p" 2>/dev/null; done
    REPORTS=(); for i in $(seq 1 "$E_SHARDS"); do REPORTS+=("$OUT/e2e-$i.json"); done
    E2E_SUMMARY="$($TOOLS e2e --list-failed "$OUT/e2e-failed.txt" "${REPORTS[@]}")"
    if [ -s "$OUT/e2e-failed.txt" ]; then
      # Specs that fail under load but pass alone: rerun just the failed ones, one worker.
      RERUN=(); while IFS=$'\t' read -r id _; do RERUN+=("$id"); done < "$OUT/e2e-failed.txt"
      mkdir -p "$RUN/tmp-er" "$RUN/scratch-er"
      TMPDIR="$RUN/tmp-er" E2E_SCRATCH="$RUN/scratch-er" E2E_WORKERS=1 E2E_REPORT="$OUT/e2e-rerun.json" \
        E2E_OUT="$RUN/e2e-out-rerun" "${PW[@]}" "${RERUN[@]}" > "$OUT/e2e-rerun.log" 2>&1
      E2E_SUMMARY="$E2E_SUMMARY"$'\n'"e2e rerun of ${#RERUN[@]} failed specs, serially:"$'\n'"$($TOOLS rerun "$OUT/e2e-rerun.json" "$OUT/e2e-failed.txt" 2> "$OUT/e2e-still.txt")"
      [ -s "$OUT/e2e-still.txt" ] && STATUS=1
    fi
    for i in $(seq 1 "$E_SHARDS"); do
      c="$(cat "$OUT/e2e-shard-$i.code" 2>/dev/null || echo 1)"
      if [ ! -s "$OUT/e2e-$i.json" ]; then E2E_SUMMARY="$E2E_SUMMARY"$'\n'"  e2e shard $i produced no report (exit $c): $OUT/e2e-shard-$i.log"; STATUS=1; fi
    done
  fi
fi
T_E2E=$((SECONDS - T_E2E_START))

# ---- summary ----
echo
echo "================ test-parallel summary ================"
if [ "$DO_BACKEND" = 1 ]; then
  $TOOLS backend "$OUT" "$B_JOBS" || STATUS=1
  $TOOLS times "$ROOT/out/test-times.json" "$OUT"/backend-shard-*.xml
fi
if [ "$DO_FRONTEND" = 1 ]; then
  for k in typecheck unit; do
    c="$(cat "$OUT/$k.code" 2>/dev/null || echo 1)"
    if [ "$c" = 0 ]; then echo "$k: ok"; else echo "$k: FAILED ($OUT/$k.log)"; STATUS=1; fi
  done
fi
[ -n "$E2E_SUMMARY" ] && echo "$E2E_SUMMARY"
PEAK="$(sort -n "$OUT/mem.log" 2>/dev/null | tail -1)"; BASE="$(head -1 "$OUT/mem.log" 2>/dev/null)"
echo "wall clock: $((SECONDS - START))s (backend+frontend ${T_BACKEND}s, e2e ${T_E2E}s); peak memory in use: ${PEAK:-?} MB (${BASE:-?} MB at start); logs: $OUT"
[ "$STATUS" = 0 ] && echo "RESULT: pass" || echo "RESULT: FAIL"
exit "$STATUS"
