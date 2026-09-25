#!/usr/bin/env bash
# Steps 27-b and 27-c acceptance: start the API against a database, run verify_api.py
# against it, and stop the server whatever happens.
#
#   DATABASE_URL=postgresql+psycopg://stockdc:stockdc@localhost:26519/stockdc_backfill \
#       scripts/verify_api.sh [port]
#
# The API key is generated for this run and never printed. The server runs in
# its own process group (setsid), and the group is killed on exit: killing the
# launcher's PID alone would leave uvicorn running.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DATABASE_URL:?set DATABASE_URL}"
PORT="${1:-8765}"
export STOCKDC_API_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(16))')"
export DATABASE_URL

setsid .venv/bin/python -m stock_data_center.api --host 127.0.0.1 --port "$PORT" >/dev/null 2>&1 &
LAUNCHER=$!
PGID="$(ps -o pgid= "$LAUNCHER" | tr -d ' ')"
cleanup() {
    kill -- "-$PGID" 2>/dev/null || true
    for _ in $(seq 50); do
        pgrep -g "$PGID" >/dev/null || return 0
        sleep 0.1
    done
    kill -9 -- "-$PGID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in $(seq 100); do
    if .venv/bin/python -c "import httpx,sys; sys.exit(httpx.get('http://127.0.0.1:$PORT/v1/datasets').status_code != 401)" 2>/dev/null; then
        break
    fi
    sleep 0.2
done

status=0
timeout 1800 .venv/bin/python scripts/verify_api.py --base-url "http://127.0.0.1:$PORT" || status=$?
cleanup
if pgrep -g "$PGID" >/dev/null; then
    echo "the API server survived its process group kill" >&2
    exit 1
fi
exit "$status"
