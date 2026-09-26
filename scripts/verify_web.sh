#!/usr/bin/env bash
# Step 37 acceptance: build the web dashboard, serve it from an API on this
# machine (same origin, as the container does), and run the Playwright checks
# that compare every drawn value with the API's answer. Stops the server
# whatever happens.
#
#   DATABASE_URL=postgresql+psycopg://stockdc:stockdc@localhost:26519/stockdc_backfill \
#       scripts/verify_web.sh [port] [-- playwright args]
#
#   STOCKDC_SCREENSHOTS=<dir>   also save screenshots of both themes, desktop and phone
#
# The API key is generated for this run and never printed. The server runs in
# its own process group (setsid), and the group is killed on exit.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DATABASE_URL:?set DATABASE_URL}"
PORT=8766
if [ $# -gt 0 ] && [ "$1" != "--" ]; then PORT="$1"; shift; fi
[ "${1:-}" = "--" ] && shift
export STOCKDC_API_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(16))')"
export DATABASE_URL

# Node may come from nvm, which only an interactive shell loads.
if ! command -v npm >/dev/null && [ -s "$HOME/.nvm/nvm.sh" ]; then
    . "$HOME/.nvm/nvm.sh"
fi
command -v npm >/dev/null || { echo "npm is not on PATH" >&2; exit 1; }
(cd web && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null)

STOCKDC_WEB_DIR="$PWD/web/dist" setsid .venv/bin/python -m stock_data_center.api \
    --host 127.0.0.1 --port "$PORT" >/dev/null 2>&1 &
LAUNCHER=$!
# setsid makes the launcher its own group leader, but only once it has run:
# until then `ps` reports this script's group, and killing that would kill the
# caller too (code review of #69). Wait for it, and never take our own group.
PGID=""
for _ in $(seq 100); do
    PGID="$(ps -o pgid= "$LAUNCHER" | tr -d ' ')"
    [ "$PGID" = "$LAUNCHER" ] && break
    sleep 0.05
done
if [ "$PGID" != "$LAUNCHER" ]; then
    echo "the API server did not get its own process group" >&2
    kill "$LAUNCHER" 2>/dev/null || true
    exit 1
fi
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
    if .venv/bin/python -c "import httpx,sys; sys.exit(httpx.get('http://127.0.0.1:$PORT/').status_code != 200)" 2>/dev/null; then
        break
    fi
    sleep 0.2
done

status=0
(cd web && STOCKDC_WEB_URL="http://127.0.0.1:$PORT" timeout 900 npx playwright test "$@") || status=$?
cleanup
if pgrep -g "$PGID" >/dev/null; then
    echo "the API server survived its process group kill" >&2
    exit 1
fi
exit "$status"
