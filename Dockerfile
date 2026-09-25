# The public API (Step 27), served by `docker compose up -d` next to PostgreSQL.
# It reads the database only; no raw file, credential or test goes in the image.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /app/src

# The commit technical-indicators-pit reports (CLAUDE.md §42); the image has
# no .git to ask. `scripts/api_up.sh` passes it.
ARG GIT_COMMIT=unknown
ENV STOCKDC_GIT_COMMIT=${GIT_COMMIT}

USER nobody
EXPOSE 28617
CMD ["python", "-m", "stock_data_center.api", "--host", "0.0.0.0", "--port", "28617"]
