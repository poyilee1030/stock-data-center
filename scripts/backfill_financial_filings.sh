#!/usr/bin/env bash
# Step 23-c — import the whole legacy iXBRL archive, one process per year.
#
# The archive is 45,324 documents and one process imports about two a second,
# so a single walk is five and a half hours. The years are independent — a
# document's identity is (filer, quarter) and no two years share one — so they
# run side by side, each resumable on its own: the CLI derives its run identity
# from its range, so rerunning this script skips what finished.
#
# CLAUDE.md §79: every run writes its own manifest, and this script exits
# non-zero if any year reported a failure.
#
# Usage: scripts/backfill_financial_filings.sh [first_year] [last_year]

set -euo pipefail

FIRST_YEAR="${1:-2020}"
LAST_YEAR="${2:-2026}"
DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://stockdc:stockdc@localhost:5432/stockdc_backfill}"
ARCHIVE_ROOT="${ARCHIVE_ROOT:-$HOME/GitHubLL/my_stock_project/data/raw/xbrl}"
RAW_ROOT="${RAW_ROOT:-data/raw}"
LOG_DIR="${LOG_DIR:-/tmp/step23c-backfill}"
# Five and a half hours is one process's whole walk; no year is that big, so a
# year still running then is stuck rather than slow.
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-21600}"

mkdir -p "$LOG_DIR"
PIDS=()

cleanup() {
    # Signal the process group, not the launcher: `python -m` forks, and a
    # wrapper PID does not forward anything.
    for pid in "${PIDS[@]:-}"; do
        [[ -n "$pid" ]] && kill -- "-$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    # Nothing may outlive this script: a stray importer keeps writing into the
    # database after the run reported itself finished.
    if pgrep -f "financial-filing-backfill" >/dev/null 2>&1; then
        pkill -9 -f "financial-filing-backfill" || true
    fi
}
trap cleanup EXIT INT TERM

for year in $(seq "$FIRST_YEAR" "$LAST_YEAR"); do
    last_quarter=4
    [[ "$year" == "2026" ]] && last_quarter=2
    setsid timeout --signal=TERM "$TIMEOUT_SECONDS" \
        .venv/bin/python -c 'import sys; from stock_data_center.ingestion.cli import main; sys.exit(main())' \
            --database-url "$DATABASE_URL" \
            --purpose gap_fill \
            financial-filing-backfill \
            --period "${year}Q1" --through "${year}Q${last_quarter}" \
            --archive-root "$ARCHIVE_ROOT" \
            --raw-root "$RAW_ROOT" \
            --progress \
        > "$LOG_DIR/${year}.json" 2> "$LOG_DIR/${year}.progress" &
    PIDS+=("$!")
    echo "launched ${year}Q1..${year}Q${last_quarter} (pgid $!)" >&2
done

status=0
for pid in "${PIDS[@]}"; do
    wait "$pid" || status=1
done

echo "--- manifests in $LOG_DIR ---" >&2
for year in $(seq "$FIRST_YEAR" "$LAST_YEAR"); do
    .venv/bin/python - "$LOG_DIR/${year}.json" <<'PY' || status=1
import json
import sys

report = json.load(open(sys.argv[1]))["backfill"]
print(
    f"{report['start']}..{report['end']}  documents={report['documents']} "
    f"imported={report['imported']} resumed={report['resumed']} "
    f"quarantined={report['quarantined']} failed={report['failed']} "
    f"facts={report['facts']}",
    file=sys.stderr,
)
sys.exit(0 if report["failed"] == 0 else 1)
PY
done

trap - EXIT
cleanup
exit "$status"
