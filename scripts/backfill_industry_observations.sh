#!/usr/bin/env bash
# Step 39-b — sweep the by-category quotes of both exchanges, side by side.
#
# TPEx: the anchor date of every ended OTC span and the reconciliation dates;
# TWSE: the anchor date of every ended listed span. About 38 pages a date, two
# (TPEx) or three (TWSE) seconds apart, one transaction per date, so a rerun
# skips every date already settled. Extra arguments go to both runs
# (`--refetch --purpose correction_check`).
#
# Exits non-zero if either run quarantined or failed a date (CLAUDE.md §79).
#
# Usage: scripts/backfill_industry_observations.sh [extra CLI arguments]

set -euo pipefail

export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://stockdc:stockdc@localhost:26519/stockdc_backfill}"
LOG_DIR="${LOG_DIR:-log}"
TAG="${TAG:-step-39-b}"
# ~60 dates x 38 pages x 2.5 s is under two hours; a run still going after
# four is stuck, not slow.
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-14400}"
PATTERN="stock_data_center.v2.industry_observations"

mkdir -p "$LOG_DIR"
PIDS=()

cleanup() {
    for pid in "${PIDS[@]:-}"; do
        [[ -n "$pid" ]] && kill -- "-$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    # `setsid` forks, so the PID above is the launcher, not the worker: this
    # reaches the worker itself.
    pkill -f -- "$PATTERN" 2>/dev/null || true
    if pgrep -f -- "$PATTERN" >/dev/null 2>&1; then
        echo "warning: still running: $PATTERN" >&2
        exit 1
    fi
}
trap cleanup EXIT INT TERM

run() {
    local source="$1"; shift
    setsid timeout "$TIMEOUT_SECONDS" .venv/bin/python -m "$PATTERN" \
        --source "$source" "$@" \
        >"$LOG_DIR/$TAG-$source.jsonl" 2>"$LOG_DIR/$TAG-$source.err" &
    PIDS+=("$!")
}

run tpex_otc_quotes --anchors --reconcile "$@"
run twse_mi_index --anchors "$@"

status=0
for pid in "${PIDS[@]}"; do
    wait "$pid" || status=1
done
exit "$status"
