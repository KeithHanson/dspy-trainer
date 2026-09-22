#!/bin/sh
set -eu

python - <<'PY'
import os
from urllib.parse import urlparse

import psycopg2

backend_uri = os.environ["MLFLOW_BACKEND_STORE_URI"]
schema_name = os.environ.get("MLFLOW_BACKEND_SCHEMA", "mlflow")
parsed = urlparse(backend_uri)

connect_kwargs = {
    "dbname": parsed.path.lstrip("/"),
    "user": parsed.username,
    "password": parsed.password,
    "host": parsed.hostname,
    "port": parsed.port or 5432,
}

with psycopg2.connect(**connect_kwargs) as conn:
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
PY

MLFLOW_WEB_WORKERS="${MLFLOW_WEB_WORKERS:-4}"
MLFLOW_STATIC_PREFIX="${MLFLOW_STATIC_PREFIX:-}"
CADDY_HTTP_PORT="${CADDY_HTTP_PORT:-8080}"
MLFLOW_ALLOWED_HOSTS="mlflow,mlflow:5000,localhost,localhost:5000,localhost:5001,localhost:${CADDY_HTTP_PORT},127.0.0.1,127.0.0.1:5000,127.0.0.1:5001,127.0.0.1:${CADDY_HTTP_PORT}"
if [ -n "${MLFLOW_ALLOWED_HOSTS_EXTRA:-}" ]; then
  MLFLOW_ALLOWED_HOSTS="${MLFLOW_ALLOWED_HOSTS},${MLFLOW_ALLOWED_HOSTS_EXTRA}"
fi
MLFLOW_CORS_ALLOWED_ORIGINS="${MLFLOW_CORS_ALLOWED_ORIGINS:-http://localhost:${CADDY_HTTP_PORT},http://127.0.0.1:${CADDY_HTTP_PORT}}"

# Only mount under a path prefix if one is explicitly set. Left empty, MLflow serves at
# root so BOTH the REST API and the span-ingest endpoint (/v1/traces, which does not honor
# --static-prefix) are reachable by internal clients. Caddy presents it under /mlflow for
# the browser by stripping that prefix and forwarding X-Forwarded-Prefix.
STATIC_PREFIX_ARG=""
if [ -n "$MLFLOW_STATIC_PREFIX" ]; then
  STATIC_PREFIX_ARG="--static-prefix $MLFLOW_STATIC_PREFIX"
fi

exec mlflow server \
  --host 0.0.0.0 \
  --port 5000 \
  --workers "$MLFLOW_WEB_WORKERS" \
  --allowed-hosts "$MLFLOW_ALLOWED_HOSTS" \
  --cors-allowed-origins "$MLFLOW_CORS_ALLOWED_ORIGINS" \
  $STATIC_PREFIX_ARG \
  --backend-store-uri "$MLFLOW_BACKEND_STORE_URI" \
  --default-artifact-root /mlflow/artifacts
