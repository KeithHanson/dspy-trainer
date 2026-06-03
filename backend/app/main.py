from contextlib import asynccontextmanager
from io import BytesIO
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.config import get_settings
from app.executor import run_bundle_eval
from app.services import AppServices
from app.validator import validate_bundle


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    services = AppServices(settings)
    await services.connect()
    app.state.services = services
    yield
    await services.disconnect()


app = FastAPI(title="dspy-trainer-backend", lifespan=lifespan)
raw_cors_origins = os.getenv(
    "DSPY_TRAINER_CORS_ALLOW_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)
cors_origins = [origin.strip() for origin in raw_cors_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


SAMPLE_BUNDLE_DIR = Path(__file__).resolve().parents[1] / "sample_bundles" / "example-bundle"


def _iter_sample_bundle_files(bundle_dir: Path) -> list[Path]:
    return sorted(path for path in bundle_dir.rglob("*") if path.is_file())


def _resolve_bundle_root(extract_root: Path) -> Path:
    direct = extract_root / "module.py"
    if direct.exists():
        return extract_root

    candidates: list[tuple[int, Path]] = []
    for module_file in extract_root.rglob("module.py"):
        parent = module_file.parent
        if (parent / "metric.py").exists():
            score = 1 if (parent / "bundle.toml").exists() else 0
            candidates.append((score, parent))

    if not candidates:
        return extract_root

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


class ModuleImportRequest(BaseModel):
    source: str
    source_ref: str | None = None
    version_hash: str | None = None


class ValidateRequest(BaseModel):
    bundle_path: str


class SmokeTestRequest(BaseModel):
    bundle_path: str
    eval_inputs: list[dict[str, Any]] = Field(default_factory=list)
    num_threads: int = 1


class OptimizationJobCreateRequest(BaseModel):
    project_id: str
    module_import_id: str
    bundle_path: str
    strategy: str = "bootstrap_fewshot"
    objective: str = "optimize_demo_quality"
    dataset_id: str | None = None
    validation_dataset_id: str | None = None
    execution_lm_profile_id: str | None = None
    helper_lm_profile_id: str | None = None
    request_config: dict[str, Any] = Field(default_factory=dict)
    normalized_config: dict[str, Any] = Field(default_factory=dict)
    train_inputs: list[dict[str, Any]] = Field(default_factory=list)
    val_inputs: list[dict[str, Any]] = Field(default_factory=list)
    num_threads: int = 1
    source_run_plan_id: str


class OptimizationDatasetCreateRequest(BaseModel):
    project_id: str
    module_import_id: str
    name: str
    dataset_kind: str
    source_type: str
    source_run_plan_ids: list[str] = Field(default_factory=list)
    source_filters: dict[str, Any] = Field(default_factory=dict)
    records: list[dict[str, Any]] = Field(default_factory=list)
    input_keys: list[str] = Field(default_factory=list)
    label_keys: list[str] = Field(default_factory=list)
    optimizer_contract: str = "dspy_example_v1"
    provenance_summary: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class OptimizationDatasetDeriveRequest(BaseModel):
    project_id: str
    module_import_id: str
    name: str = "Derived optimization dataset"
    dataset_kind: str
    source_type: str
    source_run_plan_ids: list[str] = Field(default_factory=list)
    source_filters: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None
    persist: bool = False


class MaterializeOptimizedBundleRequest(BaseModel):
    bundle_name: str | None = None
    bundle_version: str | None = None


class AgentRunPlanCreateRequest(BaseModel):
    project_id: str
    module_import_id: str
    scenario_id: str
    dataset_version: str
    bundle_path: str
    eval_inputs: list[dict[str, Any]] = Field(default_factory=list)
    evaluation_plan_id: str | None = None
    lm_profile_id: str | None = None
    runs_per_question: int = 1
    max_workers: int = 1


class EvaluationPlanCreateRequest(BaseModel):
    project_id: str
    scenario_id: str
    dataset_version: str
    name: str = "Untitled plan"
    runs_per_question: int = 1
    max_workers: int = 1
    module_import_id: str | None = None
    lm_profile_id: str | None = None
    eval_inputs: list[dict[str, Any]] = Field(default_factory=list)


class LmProfileCreateRequest(BaseModel):
    name: str
    model: str
    api_base: str
    model_type: str = "responses"
    default_params: dict[str, Any] = Field(default_factory=dict)
    lm_class_path: str | None = None
    upstream_api_key: str | None = None


class LmProfileUpdateRequest(BaseModel):
    name: str | None = None
    model: str | None = None
    api_base: str | None = None
    model_type: str | None = None
    default_params: dict[str, Any] | None = None
    lm_class_path: str | None = None
    upstream_api_key: str | None = None


class LiteLLMKeyCreateRequest(BaseModel):
    models: list[str] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    duration: str | None = None
    key_alias: str | None = None
    team_id: str | None = None
    user_id: str | None = None


class LiteLLMKeyUpdateRequest(BaseModel):
    key: str
    models: list[str] | None = None
    aliases: dict[str, str] | None = None
    metadata: dict[str, Any] | None = None
    duration: str | None = None
    max_budget: float | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready(request: Request):
    services: AppServices = request.app.state.services
    status = await services.readiness()
    payload = {
        "status": "ready" if status.ok else "not_ready",
        "checks": {
            "postgres": status.postgres,
            "redis": status.redis,
            "mlflow": status.mlflow,
            "litellm": status.litellm,
        },
    }
    if status.ok:
        return payload
    return JSONResponse(status_code=503, content=payload)


@app.get("/workers")
async def list_workers(request: Request):
    services: AppServices = request.app.state.services
    return await services.list_workers()


@app.get("/samples/module-bundle")
async def download_module_bundle_sample():
    if not SAMPLE_BUNDLE_DIR.exists() or not SAMPLE_BUNDLE_DIR.is_dir():
        return JSONResponse(status_code=500, content={"error": "sample bundle directory is missing"})

    bundle = BytesIO()
    with ZipFile(bundle, mode="w", compression=ZIP_DEFLATED) as archive:
        root_name = SAMPLE_BUNDLE_DIR.name
        for file_path in _iter_sample_bundle_files(SAMPLE_BUNDLE_DIR):
            archive_name = f"{root_name}/{file_path.relative_to(SAMPLE_BUNDLE_DIR).as_posix()}"
            archive.write(file_path, arcname=archive_name)
    headers = {"Content-Disposition": 'attachment; filename="example-bundle.zip"'}
    return Response(content=bundle.getvalue(), media_type="application/zip", headers=headers)


@app.post("/modules/import")
async def import_module(request: Request, payload: ModuleImportRequest):
    services: AppServices = request.app.state.services
    result = await services.create_module_import(payload.source, payload.source_ref, payload.version_hash)
    return {"id": result["id"], "status": result["status"]}


@app.get("/modules")
async def list_modules(request: Request):
    services: AppServices = request.app.state.services
    return await services.list_modules()


@app.get("/modules/{module_id}")
async def get_module(module_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.get_module(module_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return result


@app.get("/modules/{module_id}/files")
async def get_module_files(module_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.get_module_files(module_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return result


@app.delete("/modules/{module_id}")
async def delete_module(module_id: str, request: Request):
    services: AppServices = request.app.state.services
    deleted = await services.delete_module(module_id)
    if not deleted:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return {"id": module_id, "deleted": True}


@app.post("/modules/{module_id}/validate")
async def validate_module(module_id: str, request: Request, payload: ValidateRequest):
    services: AppServices = request.app.state.services
    report = validate_bundle(payload.bundle_path)
    await services.set_module_bundle_metadata(
        module_id,
        report.metadata.get("name") if isinstance(report.metadata.get("name"), str) else None,
        report.metadata.get("version") if isinstance(report.metadata.get("version"), str) else None,
    )
    status = "passed" if report.passed else "failed"
    found = await services.set_validation_status(module_id, status, report.diagnostics)
    if not found:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return {
        "id": module_id,
        "validation_status": status,
        "diagnostics": report.diagnostics,
        "summary": report.summary,
    }


@app.post("/modules/{module_id}/validate-upload")
async def validate_module_upload(module_id: str, request: Request, bundle: UploadFile = File(...)):
    services: AppServices = request.app.state.services
    if not bundle.filename or not bundle.filename.lower().endswith(".zip"):
        return JSONResponse(status_code=400, content={"error": "bundle must be a .zip file"})
    payload = await bundle.read()
    try:
        with TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "bundle.zip"
            archive_path.write_bytes(payload)
            with ZipFile(archive_path) as archive:
                archive.extractall(temp_dir)

            root = Path(temp_dir)
            bundle_root = _resolve_bundle_root(root)

            report = validate_bundle(str(bundle_root))
            if report.passed:
                bundles_dir = Path("/tmp/dspy-trainer/bundles")
                bundles_dir.mkdir(parents=True, exist_ok=True)
                target_dir = bundles_dir / module_id
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                shutil.copytree(bundle_root, target_dir)
                await services.set_module_source_ref(module_id, str(target_dir))
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid zip bundle"})

    await services.set_module_bundle_metadata(
        module_id,
        report.metadata.get("name") if isinstance(report.metadata.get("name"), str) else None,
        report.metadata.get("version") if isinstance(report.metadata.get("version"), str) else None,
    )

    status = "passed" if report.passed else "failed"
    found = await services.set_validation_status(module_id, status, report.diagnostics)
    if not found:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return {
        "id": module_id,
        "validation_status": status,
        "diagnostics": report.diagnostics,
        "summary": report.summary,
    }


@app.post("/modules/{module_id}/smoke-test")
async def smoke_test_module(module_id: str, request: Request, payload: SmokeTestRequest):
    services: AppServices = request.app.state.services
    found = await services.set_smoke_status(module_id, "running", [
        {
            "code": "smoke_test_started",
            "stage": "smoke_test",
            "message": "Smoke test execution started.",
            "bundle_path": payload.bundle_path,
        }
    ])
    if not found:
        return JSONResponse(status_code=404, content={"error": "module not found"})

    try:
        report = run_bundle_eval(
            bundle_path=payload.bundle_path,
            eval_inputs=payload.eval_inputs,
            num_threads=payload.num_threads,
        )
        diagnostics = [
            {
                "code": "bundle_eval_completed",
                "stage": "bundle_eval",
                "message": "Bundle evaluation completed successfully.",
                "score_pct": report["score_pct"],
                "item_count": len(report["items"]),
                "items": report["items"],
            }
        ]
        final_status = "passed"
    except Exception as exc:
        diagnostics = [
            {
                "code": "bundle_eval_failed",
                "stage": "bundle_eval",
                "message": str(exc),
            }
        ]
        final_status = "failed"

    await services.set_smoke_status(module_id, final_status, diagnostics)
    return {"id": module_id, "smoke_status": final_status, "diagnostics": diagnostics}


@app.get("/modules/{module_id}/diagnostics")
async def module_diagnostics(module_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.get_diagnostics(module_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return result


@app.get("/modules/{module_id}/agent-run-plans")
async def list_module_agent_run_plans(request: Request, module_id: str, limit: int = 50, offset: int = 0):
    services: AppServices = request.app.state.services
    safe_limit = max(1, min(limit, 500))
    safe_offset = max(0, offset)
    result = await services.list_agent_run_plans_for_module(module_import_id=module_id, limit=safe_limit, offset=safe_offset)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return result


@app.post("/optimization/jobs")
async def create_optimization_job(request: Request, payload: OptimizationJobCreateRequest):
    services: AppServices = request.app.state.services
    try:
        request_config, normalized_config = services.prepare_optimization_job_payload(
            strategy=payload.strategy,
            objective=payload.objective,
            dataset_id=payload.dataset_id,
            validation_dataset_id=payload.validation_dataset_id,
            execution_lm_profile_id=payload.execution_lm_profile_id,
            helper_lm_profile_id=payload.helper_lm_profile_id,
            request_config=payload.request_config,
            client_normalized_config=payload.normalized_config,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    storage_strategy = str(normalized_config.get("strategy", payload.strategy)).strip() or payload.strategy.strip() or "bootstrap_fewshot"
    result = await services.create_optimization_job(
        project_id=payload.project_id,
        module_import_id=payload.module_import_id,
        bundle_path=payload.bundle_path,
        strategy=storage_strategy,
        objective=payload.objective,
        dataset_id=payload.dataset_id,
        validation_dataset_id=payload.validation_dataset_id,
        execution_lm_profile_id=payload.execution_lm_profile_id,
        helper_lm_profile_id=payload.helper_lm_profile_id,
        request_config=request_config,
        normalized_config=normalized_config,
        train_inputs=payload.train_inputs,
        val_inputs=payload.val_inputs,
        num_threads=payload.num_threads,
        source_run_plan_id=payload.source_run_plan_id,
    )
    if result is None:
        return JSONResponse(status_code=404, content={"error": "module, dataset, or lm profile not found"})
    await services.enqueue_optimization_job(str(result["id"]))
    return result


@app.get("/optimization/jobs")
async def list_optimization_jobs(request: Request, limit: int = 50, offset: int = 0):
    services: AppServices = request.app.state.services
    safe_limit = max(1, min(limit, 500))
    safe_offset = max(0, offset)
    return await services.list_optimization_jobs(limit=safe_limit, offset=safe_offset)


@app.get("/optimization/jobs/{optimization_job_id}")
async def get_optimization_job(optimization_job_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.get_optimization_job(optimization_job_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "optimization job not found"})
    return result


@app.post("/optimization/jobs/{optimization_job_id}/cancel")
async def cancel_optimization_job(optimization_job_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.cancel_optimization_job(optimization_job_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "optimization job not found"})
    return result


@app.post("/optimization/jobs/{optimization_job_id}/materialize-bundle")
async def materialize_optimized_bundle(
    optimization_job_id: str,
    request: Request,
    payload: MaterializeOptimizedBundleRequest,
):
    services: AppServices = request.app.state.services
    job = await services.get_optimization_job(optimization_job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "optimization job not found"})
    if str(job.get("status")) != "succeeded":
        return JSONResponse(status_code=409, content={"error": "only succeeded optimization jobs can create optimized bundles"})
    if not str(job.get("artifact_path") or "").strip():
        return JSONResponse(status_code=409, content={"error": "optimization job has no saved artifact to materialize"})
    result = await services.materialize_optimized_bundle(
        optimization_job_id,
        bundle_name=payload.bundle_name,
        bundle_version=payload.bundle_version,
    )
    if result is None:
        return JSONResponse(status_code=400, content={"error": "optimized bundle could not be materialized"})
    return result


@app.delete("/optimization/jobs/{optimization_job_id}")
async def delete_optimization_job(optimization_job_id: str, request: Request):
    services: AppServices = request.app.state.services
    deleted = await services.delete_optimization_job(optimization_job_id)
    if not deleted:
        return JSONResponse(status_code=404, content={"error": "optimization job not found"})
    return {"id": optimization_job_id, "deleted": True}


@app.post("/optimization/jobs/{optimization_job_id}/run")
async def run_optimization_job_endpoint(optimization_job_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.run_optimization_job(optimization_job_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "optimization job not found"})
    return result


@app.post("/optimization/datasets")
async def create_optimization_dataset(request: Request, payload: OptimizationDatasetCreateRequest):
    services: AppServices = request.app.state.services
    result = await services.create_optimization_dataset(
        project_id=payload.project_id,
        module_import_id=payload.module_import_id,
        name=payload.name,
        dataset_kind=payload.dataset_kind,
        source_type=payload.source_type,
        source_run_plan_ids=payload.source_run_plan_ids,
        source_filters=payload.source_filters,
        records=payload.records,
        input_keys=payload.input_keys,
        label_keys=payload.label_keys,
        optimizer_contract=payload.optimizer_contract,
        provenance_summary=payload.provenance_summary,
        notes=payload.notes,
    )
    if result is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return result


@app.post("/optimization/datasets/derive")
async def derive_optimization_dataset(request: Request, payload: OptimizationDatasetDeriveRequest):
    services: AppServices = request.app.state.services
    result = await services.derive_optimization_dataset(
        project_id=payload.project_id,
        module_import_id=payload.module_import_id,
        name=payload.name,
        dataset_kind=payload.dataset_kind,
        source_type=payload.source_type,
        source_run_plan_ids=payload.source_run_plan_ids,
        source_filters=payload.source_filters,
        notes=payload.notes,
        persist=payload.persist,
    )
    if result is None:
        return JSONResponse(status_code=404, content={"error": "run plan or module not found"})
    return result


@app.get("/optimization/datasets")
async def list_optimization_datasets(request: Request, limit: int = 50, offset: int = 0):
    services: AppServices = request.app.state.services
    safe_limit = max(1, min(limit, 500))
    safe_offset = max(0, offset)
    return await services.list_optimization_datasets(limit=safe_limit, offset=safe_offset)


@app.get("/optimization/datasets/{dataset_id}")
async def get_optimization_dataset(dataset_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.get_optimization_dataset(dataset_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "optimization dataset not found"})
    return result


@app.post("/agent-run-plans")
async def create_agent_run_plan(request: Request, payload: AgentRunPlanCreateRequest):
    services: AppServices = request.app.state.services
    result = await services.create_agent_run_plan(
        project_id=payload.project_id,
        module_import_id=payload.module_import_id,
        scenario_id=payload.scenario_id,
        dataset_version=payload.dataset_version,
        bundle_path=payload.bundle_path,
        eval_inputs=payload.eval_inputs,
        evaluation_plan_id=payload.evaluation_plan_id,
        lm_profile_id=payload.lm_profile_id,
        runs_per_question=payload.runs_per_question,
        max_workers=payload.max_workers,
    )
    if result is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return result


@app.get("/agent-run-plans/{plan_id}")
async def get_agent_run_plan(plan_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.get_agent_run_plan(plan_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "agent run plan not found"})
    return result


@app.get("/agent-run-plans")
async def list_agent_run_plans(request: Request, limit: int = 50, offset: int = 0):
    services: AppServices = request.app.state.services
    return await services.list_agent_run_plans(limit=limit, offset=offset)


@app.delete("/agent-run-plans/{plan_id}")
async def delete_agent_run_plan(plan_id: str, request: Request):
    services: AppServices = request.app.state.services
    deleted = await services.delete_agent_run_plan(plan_id)
    if not deleted:
        return JSONResponse(status_code=404, content={"error": "agent run plan not found"})
    return {"id": plan_id, "deleted": True}


@app.post("/agent-run-plans/{plan_id}/enqueue")
async def enqueue_agent_run_plan(plan_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.enqueue_agent_run_plan(plan_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "agent run plan not found"})
    return result


@app.get("/agent-run-plans/{plan_id}/tasks")
async def list_agent_run_plan_tasks(plan_id: str, request: Request, limit: int = 50, offset: int = 0):
    services: AppServices = request.app.state.services
    safe_limit = max(1, min(limit, 500))
    safe_offset = max(0, offset)
    result = await services.list_agent_run_tasks(plan_id, safe_limit, safe_offset)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "agent run plan not found"})
    return result


@app.post("/evaluation-plans")
async def create_evaluation_plan(request: Request, payload: EvaluationPlanCreateRequest):
    services: AppServices = request.app.state.services
    try:
        result = await services.create_evaluation_plan(
            project_id=payload.project_id,
            scenario_id=payload.scenario_id,
            dataset_version=payload.dataset_version,
            name=payload.name,
            runs_per_question=payload.runs_per_question,
            max_workers=payload.max_workers,
            module_import_id=payload.module_import_id,
            lm_profile_id=payload.lm_profile_id,
            eval_inputs=payload.eval_inputs,
        )
    except ValueError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    return result


@app.post("/lm-profiles")
async def create_lm_profile(request: Request, payload: LmProfileCreateRequest):
    services: AppServices = request.app.state.services
    if not payload.upstream_api_key or not payload.upstream_api_key.strip():
        return JSONResponse(status_code=400, content={"error": "upstream_api_key is required when creating an lm profile"})
    try:
        return await services.create_lm_profile(
            name=payload.name,
            model=payload.model,
            api_base=payload.api_base,
            model_type=payload.model_type,
            default_params=payload.default_params,
            lm_class_path=payload.lm_class_path,
            upstream_api_key=payload.upstream_api_key,
        )
    except RuntimeError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})


@app.get("/lm-profiles")
async def list_lm_profiles(request: Request):
    services: AppServices = request.app.state.services
    return await services.list_lm_profiles()


@app.get("/lm-profiles/{lm_profile_id}")
async def get_lm_profile(lm_profile_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.get_lm_profile(lm_profile_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "lm profile not found"})
    return result


@app.patch("/lm-profiles/{lm_profile_id}")
async def update_lm_profile(lm_profile_id: str, request: Request, payload: LmProfileUpdateRequest):
    services: AppServices = request.app.state.services
    try:
        result = await services.update_lm_profile(
            lm_profile_id=lm_profile_id,
            name=payload.name,
            model=payload.model,
            api_base=payload.api_base,
            model_type=payload.model_type,
            default_params=payload.default_params,
            lm_class_path=payload.lm_class_path,
            upstream_api_key=payload.upstream_api_key,
        )
    except RuntimeError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    if result is None:
        return JSONResponse(status_code=404, content={"error": "lm profile not found"})
    return result


@app.delete("/lm-profiles/{lm_profile_id}")
async def delete_lm_profile(lm_profile_id: str, request: Request):
    services: AppServices = request.app.state.services
    deleted = await services.delete_lm_profile(lm_profile_id)
    if not deleted:
        return JSONResponse(status_code=404, content={"error": "lm profile not found"})
    return {"id": lm_profile_id, "deleted": True}


@app.post("/lm-profiles/{lm_profile_id}/rotate-key")
async def rotate_lm_profile_key(lm_profile_id: str, request: Request):
    services: AppServices = request.app.state.services
    try:
        result = await services.rotate_lm_profile_virtual_key(lm_profile_id)
    except RuntimeError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    if result is None:
        return JSONResponse(status_code=404, content={"error": "lm profile not found"})
    return result


@app.post("/lm-profiles/{lm_profile_id}/test-connection")
async def test_lm_profile_connection(lm_profile_id: str, request: Request):
    services: AppServices = request.app.state.services
    try:
        result = await services.test_lm_profile_connection(lm_profile_id)
    except RuntimeError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    if result is None:
        return JSONResponse(status_code=404, content={"error": "lm profile not found"})
    return result


@app.get("/litellm/keys")
async def list_litellm_keys(request: Request):
    services: AppServices = request.app.state.services
    return await services.list_litellm_keys()


@app.post("/litellm/keys")
async def create_litellm_key(request: Request, payload: LiteLLMKeyCreateRequest):
    services: AppServices = request.app.state.services
    return await services.create_litellm_key(
        models=payload.models,
        aliases=payload.aliases,
        metadata=payload.metadata,
        duration=payload.duration,
        key_alias=payload.key_alias,
        team_id=payload.team_id,
        user_id=payload.user_id,
    )


@app.get("/litellm/keys/{key}")
async def get_litellm_key(key: str, request: Request):
    services: AppServices = request.app.state.services
    return await services.get_litellm_key_info(key)


@app.patch("/litellm/keys/{key}")
async def update_litellm_key(key: str, request: Request, payload: LiteLLMKeyUpdateRequest):
    services: AppServices = request.app.state.services
    effective_key = payload.key or key
    if effective_key != key:
        return JSONResponse(status_code=400, content={"error": "path key and payload key must match"})
    return await services.update_litellm_key(
        key=effective_key,
        models=payload.models,
        aliases=payload.aliases,
        metadata=payload.metadata,
        duration=payload.duration,
        max_budget=payload.max_budget,
        rpm_limit=payload.rpm_limit,
        tpm_limit=payload.tpm_limit,
    )


@app.post("/litellm/keys/{key}/revoke")
async def revoke_litellm_key(key: str, request: Request):
    services: AppServices = request.app.state.services
    return await services.revoke_litellm_key(key)


@app.post("/litellm/keys/{key}/restore")
async def restore_litellm_key(key: str, request: Request):
    services: AppServices = request.app.state.services
    return await services.restore_litellm_key(key)


@app.get("/evaluation-plans")
async def list_evaluation_plans(request: Request):
    services: AppServices = request.app.state.services
    return await services.list_evaluation_plans()


@app.get("/evaluation-plans/{evaluation_plan_id}")
async def get_evaluation_plan(evaluation_plan_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.get_evaluation_plan(evaluation_plan_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "evaluation plan not found"})
    return result


@app.patch("/evaluation-plans/{evaluation_plan_id}")
async def update_evaluation_plan(evaluation_plan_id: str, request: Request, payload: EvaluationPlanCreateRequest):
    services: AppServices = request.app.state.services
    try:
        result = await services.update_evaluation_plan(
            evaluation_plan_id=evaluation_plan_id,
            project_id=payload.project_id,
            scenario_id=payload.scenario_id,
            dataset_version=payload.dataset_version,
            name=payload.name,
            runs_per_question=payload.runs_per_question,
            max_workers=payload.max_workers,
            module_import_id=payload.module_import_id,
            lm_profile_id=payload.lm_profile_id,
            eval_inputs=payload.eval_inputs,
        )
    except ValueError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    if result is None:
        return JSONResponse(status_code=404, content={"error": "evaluation plan not found"})
    return result


@app.delete("/evaluation-plans/{evaluation_plan_id}")
async def delete_evaluation_plan(evaluation_plan_id: str, request: Request):
    services: AppServices = request.app.state.services
    deleted = await services.delete_evaluation_plan(evaluation_plan_id)
    if not deleted:
        return JSONResponse(status_code=404, content={"error": "evaluation plan not found"})
    return {"id": evaluation_plan_id, "deleted": True}
