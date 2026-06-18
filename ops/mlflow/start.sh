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

exec mlflow server \
  --host 0.0.0.0 \
  --port 5000 \
  --workers "$MLFLOW_WEB_WORKERS" \
  --allowed-hosts mlflow,mlflow:5000,localhost,localhost:5000,localhost:5001,127.0.0.1,127.0.0.1:5000,127.0.0.1:5001 \
  --backend-store-uri "$MLFLOW_BACKEND_STORE_URI" \
  --default-artifact-root /mlflow/artifacts
