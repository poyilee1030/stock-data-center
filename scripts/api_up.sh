#!/usr/bin/env bash
# Build and start the API container with the commit it serves (CLAUDE.md §42):
# the image has no .git, so the commit is passed in, `-dirty` if the tree is.
#
#   scripts/api_up.sh            # docker compose up -d --build api
set -euo pipefail
cd "$(dirname "$0")/.."
GIT_COMMIT="$(git rev-parse HEAD)"
if [ -n "$(git status --porcelain)" ]; then
    GIT_COMMIT="$GIT_COMMIT-dirty"
fi
export GIT_COMMIT
docker compose up -d --build api
