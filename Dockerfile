# The public API (Step 27) and the web dashboard (Step 37, ADR-0029), served by
# `docker compose up -d` next to PostgreSQL. It reads the database only; no raw
# file, credential or test goes in the image.

# The dashboard is built here; the API image carries only its static files.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/index.html web/vite.config.ts ./
COPY web/src ./src
# Type checking and tests run in scripts/verify_web.sh; the image only builds.
RUN npx vite build

FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /app/src
COPY --from=web /web/dist /app/web

# The commit technical-indicators-pit reports (CLAUDE.md §42); the image has
# no .git to ask. `scripts/api_up.sh` passes it.
ARG GIT_COMMIT=unknown
ENV STOCKDC_GIT_COMMIT=${GIT_COMMIT}
ENV STOCKDC_WEB_DIR=/app/web

USER nobody
EXPOSE 28617
CMD ["python", "-m", "stock_data_center.api", "--host", "0.0.0.0", "--port", "28617"]
