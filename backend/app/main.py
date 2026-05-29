from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.executor import run_bundle_eval, run_eval_job
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


class EvalJobCreateRequest(BaseModel):
    project_id: str
    module_import_id: str
    scenario_id: str
    dataset_version: str
    bundle_path: str
    repeat_count: int = 1
    num_threads: int = 1
    eval_inputs: list[dict[str, Any]] = Field(default_factory=list)
    mlflow_experiment_id: str | None = None
    mlflow_parent_run_id: str | None = None


class OptimizationJobCreateRequest(BaseModel):
    project_id: str
    module_import_id: str
    bundle_path: str
    train_inputs: list[dict[str, Any]] = Field(default_factory=list)
    val_inputs: list[dict[str, Any]] = Field(default_factory=list)
    num_threads: int = 1
    source_eval_job_id: str | None = None


class AgentRunPlanCreateRequest(BaseModel):
    project_id: str
    module_import_id: str
    scenario_id: str
    dataset_version: str
    bundle_path: str
    eval_inputs: list[dict[str, Any]] = Field(default_factory=list)
    runs_per_question: int = 1
    max_workers: int = 1


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


@app.post("/modules/import")
async def import_module(request: Request, payload: ModuleImportRequest):
    services: AppServices = request.app.state.services
    result = await services.create_module_import(payload.source, payload.source_ref, payload.version_hash)
    return {"id": result["id"], "status": result["status"]}


@app.post("/modules/{module_id}/validate")
async def validate_module(module_id: str, request: Request, payload: ValidateRequest):
    services: AppServices = request.app.state.services
    report = validate_bundle(payload.bundle_path)
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
                "judge_instructions": report["judge_instructions"],
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


@app.post("/eval/jobs")
async def create_eval_job(request: Request, payload: EvalJobCreateRequest):
    services: AppServices = request.app.state.services
    result = await services.create_eval_job(
        project_id=payload.project_id,
        module_import_id=payload.module_import_id,
        scenario_id=payload.scenario_id,
        dataset_version=payload.dataset_version,
        bundle_path=payload.bundle_path,
        repeat_count=payload.repeat_count,
        num_threads=payload.num_threads,
        eval_inputs=payload.eval_inputs,
        mlflow_experiment_id=payload.mlflow_experiment_id,
        mlflow_parent_run_id=payload.mlflow_parent_run_id,
    )
    if result is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return result


@app.get("/eval/jobs/{eval_job_id}")
async def get_eval_job(eval_job_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.get_eval_job(eval_job_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "eval job not found"})
    return result


@app.post("/eval/jobs/{eval_job_id}/cancel")
async def cancel_eval_job(eval_job_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.cancel_eval_job(eval_job_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "eval job not found"})
    return result


@app.get("/eval/jobs/{eval_job_id}/items")
async def list_eval_job_items(eval_job_id: str, request: Request, limit: int = 50, offset: int = 0):
    services: AppServices = request.app.state.services
    safe_limit = max(1, min(limit, 500))
    safe_offset = max(0, offset)
    result = await services.list_eval_run_items(eval_job_id, limit=safe_limit, offset=safe_offset)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "eval job not found"})
    return result


@app.post("/eval/jobs/{eval_job_id}/run")
async def run_eval_job_endpoint(eval_job_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await run_eval_job(services, eval_job_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "eval job not found"})
    return result


@app.post("/optimization/jobs")
async def create_optimization_job(request: Request, payload: OptimizationJobCreateRequest):
    services: AppServices = request.app.state.services
    result = await services.create_optimization_job(
        project_id=payload.project_id,
        module_import_id=payload.module_import_id,
        bundle_path=payload.bundle_path,
        train_inputs=payload.train_inputs,
        val_inputs=payload.val_inputs,
        num_threads=payload.num_threads,
        source_eval_job_id=payload.source_eval_job_id,
    )
    if result is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return result


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


@app.post("/optimization/jobs/{optimization_job_id}/run")
async def run_optimization_job_endpoint(optimization_job_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.run_optimization_job(optimization_job_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "optimization job not found"})
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
