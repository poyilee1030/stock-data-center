#!/usr/bin/env bash
# Materialise technical_indicators:v1 for both daily-price sources, sharded.
#
# Step 26-a. Securities are independent of one another, so the only reason the
# run is slow is that it is one process: shards split the sorted security list
# by position, write disjoint rows, and finish in a quarter of the time.
#
# Self-terminating: every shard runs under its own process group, the whole run
# is bounded by a timeout, and the trap signals the groups rather than the
# wrapper's own pid — which would not forward anything — and then verifies that
# nothing is left behind before exiting.
#
#   scripts/materialize_technical_indicators.sh <database-url> <knowledge-as-of> [shards]
set -euo pipefail

DATABASE_URL="${1:?usage: $0 <database-url> <knowledge-as-of> [shards]}"
KNOWLEDGE_AS_OF="${2:?an ISO instant with an offset, e.g. 2026-09-22T00:00:00+08:00}"
SHARDS="${3:-4}"
START="${START:-2020-01-02}"
END="${END:-2026-09-11}"
LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-86400}"
PYTHON="${PYTHON:-.venv/bin/python}"

pids=()

cleanup() {
    for pid in "${pids[@]:-}"; do
        [ -n "$pid" ] || continue
        kill -- "-$pid" 2>/dev/null || true
    done
    sleep 2
    if pgrep -f "stock_data_center.derived.cli" >/dev/null; then
        pkill -f "stock_data_center.derived.cli" || true
        sleep 2
    fi
    if pgrep -f "stock_data_center.derived.cli" >/dev/null; then
        echo "residual workers survived cleanup" >&2
        exit 1
    fi
}
trap cleanup EXIT INT TERM

mkdir -p "$LOG_DIR"
echo "logs in $LOG_DIR"
for source in twse_mi_index tpex_otc_quotes; do
    for shard in $(seq 0 $((SHARDS - 1))); do
        log="$LOG_DIR/$source.$shard.log"
        setsid timeout "$TIMEOUT_SECONDS" "$PYTHON" -m stock_data_center.derived.cli \
            --database-url "$DATABASE_URL" \
            technical-indicators \
            --source "$source" \
            --start "$START" --end "$END" \
            --knowledge-as-of "$KNOWLEDGE_AS_OF" \
            --shard "$shard" --shards "$SHARDS" \
            --progress > "$log" 2>&1 &
        pids+=("$!")
    done
    # One source at a time: both at once would put 2 x SHARDS writers on the
    # same table and the index maintenance, not the reads, is the bottleneck.
    status=0
    for pid in "${pids[@]}"; do
        wait "$pid" || status=$?
    done
    pids=()
    echo "$source finished with status $status"
done

grep -h metric_rows_written "$LOG_DIR"/*.log || true
