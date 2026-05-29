#!/usr/bin/env bash
set -euo pipefail

API_BASE="${API_BASE:-http://localhost:8000}"
BUNDLE_PATH="${BUNDLE_PATH:-examples/module_bundles/simple_echo_agent}"
EVAL_INPUTS_PATH="${EVAL_INPUTS_PATH:-${BUNDLE_PATH}/eval_inputs.json}"
PROJECT_ID="${PROJECT_ID:-example-project}"
SCENARIO_ID="${SCENARIO_ID:-example-scenario}"
DATASET_VERSION="${DATASET_VERSION:-v1}"
RUNS_PER_QUESTION="${RUNS_PER_QUESTION:-2}"
MAX_WORKERS="${MAX_WORKERS:-2}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-2}"
POLL_TIMEOUT_SECONDS="${POLL_TIMEOUT_SECONDS:-180}"

log() {
  printf '%s [agent-plan] %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

check_api_ready() {
  local health
  if ! health="$(curl -sS --max-time 5 "${API_BASE}/health")"; then
    fail "cannot reach backend at ${API_BASE}. Start services with: docker compose up -d --build backend worker"
  fi
  local status
  status="$(python -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' <<<"${health}" 2>/dev/null || true)"
  [[ "${status}" == "ok" ]] || fail "backend health check failed at ${API_BASE}/health: ${health}"
}

command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v python >/dev/null 2>&1 || fail "python is required"

[[ -f "${BUNDLE_PATH}/module.py" ]] || fail "missing bundle file: ${BUNDLE_PATH}/module.py"
[[ -f "${BUNDLE_PATH}/metric.py" ]] || fail "missing bundle file: ${BUNDLE_PATH}/metric.py"
[[ -f "${EVAL_INPUTS_PATH}" ]] || fail "missing eval inputs file: ${EVAL_INPUTS_PATH}"

log "Starting agent-run-plan workflow"
log "API_BASE=${API_BASE}"
log "BUNDLE_PATH=${BUNDLE_PATH}"
log "EVAL_INPUTS_PATH=${EVAL_INPUTS_PATH}"
log "PROJECT_ID=${PROJECT_ID}, SCENARIO_ID=${SCENARIO_ID}, DATASET_VERSION=${DATASET_VERSION}"
log "RUNS_PER_QUESTION=${RUNS_PER_QUESTION}, MAX_WORKERS=${MAX_WORKERS}"
log "Checking backend health"
check_api_ready
log "Backend is reachable"

log "Step 1/5: Importing module bundle"
MODULE_IMPORT_JSON="$(curl -sS -X POST "${API_BASE}/modules/import" \
  -H "Content-Type: application/json" \
  -d "$(python -c 'import json,sys; print(json.dumps({"source":"local","source_ref":sys.argv[1]}))' "${BUNDLE_PATH}")")"
MODULE_ID="$(python -c 'import json,sys; data=json.load(sys.stdin); print(data.get("id",""))' <<<"${MODULE_IMPORT_JSON}")"
[[ -n "${MODULE_ID}" ]] || fail "module import failed: ${MODULE_IMPORT_JSON}"
log "MODULE_ID=${MODULE_ID}"

log "Step 2/5: Validating module + judge contract"
VALIDATION_JSON="$(curl -sS -X POST "${API_BASE}/modules/${MODULE_ID}/validate" \
  -H "Content-Type: application/json" \
  -d "$(python -c 'import json,sys; print(json.dumps({"bundle_path":sys.argv[1]}))' "${BUNDLE_PATH}")")"
python -m json.tool <<<"${VALIDATION_JSON}"
VALIDATION_STATUS="$(python -c 'import json,sys; print(json.load(sys.stdin).get("validation_status", ""))' <<<"${VALIDATION_JSON}")"
[[ "${VALIDATION_STATUS}" == "passed" ]] || fail "module validation failed"
log "Validation passed"

log "Step 3/5: Creating agent run plan"
PLAN_JSON="$(python - "${API_BASE}" "${PROJECT_ID}" "${MODULE_ID}" "${SCENARIO_ID}" "${DATASET_VERSION}" "${BUNDLE_PATH}" "${EVAL_INPUTS_PATH}" "${RUNS_PER_QUESTION}" "${MAX_WORKERS}" <<'PY'
import json
import subprocess
import sys

api_base, project_id, module_id, scenario_id, dataset_version, bundle_path, eval_inputs_path, runs_per_question, max_workers = sys.argv[1:10]
with open(eval_inputs_path, encoding="utf-8") as fh:
    eval_inputs = json.load(fh)

payload = {
    "project_id": project_id,
    "module_import_id": module_id,
    "scenario_id": scenario_id,
    "dataset_version": dataset_version,
    "bundle_path": bundle_path,
    "eval_inputs": eval_inputs,
    "runs_per_question": int(runs_per_question),
    "max_workers": int(max_workers),
}

res = subprocess.run(
    [
        "curl",
        "-sS",
        "-X",
        "POST",
        f"{api_base}/agent-run-plans",
        "-H",
        "Content-Type: application/json",
        "-d",
        json.dumps(payload),
    ],
    check=True,
    capture_output=True,
    text=True,
)
print(res.stdout)
PY
)"
PLAN_ID="$(python -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"${PLAN_JSON}")"
log "PLAN_ID=${PLAN_ID}"
python -m json.tool <<<"${PLAN_JSON}"

log "Step 4/5: Enqueuing plan"
ENQUEUE_JSON="$(curl -sS -X POST "${API_BASE}/agent-run-plans/${PLAN_ID}/enqueue")"
python -m json.tool <<<"${ENQUEUE_JSON}" >/dev/null 2>&1 || fail "enqueue failed with non-JSON response: ${ENQUEUE_JSON}"
python -m json.tool <<<"${ENQUEUE_JSON}"
TOTAL_TASKS="$(python -c 'import json,sys; print(json.load(sys.stdin).get("total_tasks", 0))' <<<"${ENQUEUE_JSON}")"
log "Enqueued plan with TOTAL_TASKS=${TOTAL_TASKS}"

log "Step 5/5: Polling until plan is terminal or timeout"
START_TS="$(date +%s)"
while true; do
  PLAN_STATE_JSON="$(curl -sS "${API_BASE}/agent-run-plans/${PLAN_ID}")"
  PLAN_STATUS="$(python -c 'import json,sys; print(json.load(sys.stdin).get("status", ""))' <<<"${PLAN_STATE_JSON}")"
  COMPLETED_TASKS="$(python -c 'import json,sys; print(json.load(sys.stdin).get("completed_tasks", 0))' <<<"${PLAN_STATE_JSON}")"
  FAILED_TASKS="$(python -c 'import json,sys; print(json.load(sys.stdin).get("failed_tasks", 0))' <<<"${PLAN_STATE_JSON}")"
  NOW_TS="$(date +%s)"
  ELAPSED="$((NOW_TS - START_TS))"

  log "Plan status=${PLAN_STATUS} completed=${COMPLETED_TASKS} failed=${FAILED_TASKS} elapsed=${ELAPSED}s"

  if [[ "${PLAN_STATUS}" == "succeeded" || "${PLAN_STATUS}" == "failed" ]]; then
    log "Plan reached terminal status=${PLAN_STATUS}"
    break
  fi

  if (( ELAPSED >= POLL_TIMEOUT_SECONDS )); then
    fail "timed out waiting for plan to finish after ${POLL_TIMEOUT_SECONDS}s"
  fi

  sleep "${POLL_INTERVAL_SECONDS}"
done

log "Fetching final task list"
TASKS_JSON="$(curl -sS "${API_BASE}/agent-run-plans/${PLAN_ID}/tasks?limit=500&offset=0")"
python -m json.tool <<<"${TASKS_JSON}"

log "Workflow complete for PLAN_ID=${PLAN_ID}"
