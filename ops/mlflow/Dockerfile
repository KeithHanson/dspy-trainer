FROM ghcr.io/mlflow/mlflow:latest

# The upstream MLflow image does not include a PostgreSQL driver. Both the
# schema bootstrap in start.sh and MLflow's SQLAlchemy backend store need it.
RUN python -m pip install --no-cache-dir psycopg2-binary==2.9.10
