#!/usr/bin/env bash
set -euo pipefail

API_BASE="${API_BASE:-http://localhost:8000}"
BUNDLE_PATH="${BUNDLE_PATH:-examples/module_bundles/simple_echo_agent}"
EVAL_INPUTS_PATH="${EVAL_INPUTS_PATH:-${BUNDLE_PATH}/eval_inputs.json}"

if [[ ! -f "${BUNDLE_PATH}/module.py" ]]; then
  echo "missing bundle file: ${BUNDLE_PATH}/module.py" >&2
  exit 1
fi

if [[ ! -f "${BUNDLE_PATH}/metric.py" ]]; then
  echo "missing bundle file: ${BUNDLE_PATH}/metric.py" >&2
  exit 1
fi

if [[ ! -f "${EVAL_INPUTS_PATH}" ]]; then
  echo "missing eval inputs file: ${EVAL_INPUTS_PATH}" >&2
  exit 1
fi

echo "Importing module bundle from ${BUNDLE_PATH}"
MODULE_ID="$(curl -sS -X POST "${API_BASE}/modules/import" \
  -H "Content-Type: application/json" \
  -d "$(python -c 'import json,sys; print(json.dumps({"source":"local","source_ref":sys.argv[1]}))' "${BUNDLE_PATH}")" \
  | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

echo "MODULE_ID=${MODULE_ID}"

echo "Validating module + judge contract"
VALIDATION_JSON="$(curl -sS -X POST "${API_BASE}/modules/${MODULE_ID}/validate" \
  -H "Content-Type: application/json" \
  -d "$(python -c 'import json,sys; print(json.dumps({"bundle_path":sys.argv[1]}))' "${BUNDLE_PATH}")")"
python -m json.tool <<<"${VALIDATION_JSON}"

VALIDATION_STATUS="$(python -c 'import json,sys; print(json.load(sys.stdin).get("validation_status", ""))' <<<"${VALIDATION_JSON}")"
if [[ "${VALIDATION_STATUS}" != "passed" ]]; then
  echo "module validation failed; not creating eval job" >&2
  exit 1
fi

echo "Creating eval job"
EVAL_JOB_ID="$(python - "${API_BASE}" "${MODULE_ID}" "${BUNDLE_PATH}" "${EVAL_INPUTS_PATH}" <<'PY'
import json
import subprocess
import sys

api_base, module_id, bundle_path, eval_inputs_path = sys.argv[1:5]
with open(eval_inputs_path, encoding="utf-8") as fh:
    eval_inputs = json.load(fh)

payload = {
    "project_id": "example-project",
    "module_import_id": module_id,
    "scenario_id": "example-scenario",
    "dataset_version": "v1",
    "bundle_path": bundle_path,
    "repeat_count": 1,
    "num_threads": 1,
    "eval_inputs": eval_inputs,
}

res = subprocess.run(
    [
        "curl",
        "-sS",
        "-X",
        "POST",
        f"{api_base}/eval/jobs",
        "-H",
        "Content-Type: application/json",
        "-d",
        json.dumps(payload),
    ],
    check=True,
    capture_output=True,
    text=True,
)
print(json.loads(res.stdout)["id"])
PY
)"

echo "EVAL_JOB_ID=${EVAL_JOB_ID}"

echo "Running eval job"
curl -sS -X POST "${API_BASE}/eval/jobs/${EVAL_JOB_ID}/run" | python -m json.tool

echo "Fetching eval run items"
curl -sS "${API_BASE}/eval/jobs/${EVAL_JOB_ID}/items" | python -m json.tool
