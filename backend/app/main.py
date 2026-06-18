from contextlib import asynccontextmanager
from io import BytesIO
import asyncio
import json
import os
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.executor import run_bundle_eval
from app.services import AppServices, ModuleSyncError
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
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
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


def _read_endpoint_api_key(request: Request) -> str:
    auth_header = str(request.headers.get("authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return str(request.headers.get("x-endpoint-key") or "").strip()


def _format_sse_event(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _iter_sample_bundle_files(bundle_dir: Path) -> list[Path]:
    return sorted(path for path in bundle_dir.rglob("*") if path.is_file())


class ModuleImportRequest(BaseModel):
    source: str
    source_ref: str | None = None
    version_hash: str | None = None
    github_repo_url: str | None = None
    github_branch: str | None = None
    github_subpath: str | None = None
    checkout_path: str | None = None
    current_commit_sha: str | None = None
    upstream_commit_sha: str | None = None
    sync_status: str | None = None
    github_secrets_environment_name: str | None = None
    environment_entries: list[dict[str, Any]] | None = None


class ValidateRequest(BaseModel):
    bundle_path: str


class SmokeTestRequest(BaseModel):
    bundle_path: str
    eval_inputs: list[dict[str, Any]] = Field(default_factory=list)
    num_threads: int = 1


class ModuleMetadataUpdateRequest(BaseModel):
    bundle_name: str | None = None
    bundle_version: str | None = None
    github_secrets_environment_name: str | None = None
    environment_entries: list[dict[str, Any]] | None = None


class ModuleSyncRequest(BaseModel):
    force: bool | None = None


class BundleEndpointCreateRequest(BaseModel):
    name: str
    module_import_id: str | None = None
    lm_profile_id: str | None = None
    pinned_worker_count: int = 1


class BundleEndpointUpdateRequest(BaseModel):
    name: str | None = None
    module_import_id: str | None = None
    lm_profile_id: str | None = None
    pinned_worker_count: int | None = None


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
    module_import_id: str
    dataset_id: str
    lm_profile_id: str | None = None


class EvaluationDatasetCreateRequest(BaseModel):
    project_id: str
    name: str
    description: str | None = None
    module_import_id: str
    records: list[dict[str, Any]] = Field(default_factory=list)


class EvaluationDatasetDuplicateRequest(BaseModel):
    name: str | None = None


class EvaluationPlanGenerateRowsRequest(BaseModel):
    lm_profile_id: str
    module_import_id: str | None = None
    operator_prompt: str
    existing_rows: list[dict[str, Any]] = Field(default_factory=list)
    max_rows: int = 5


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
        "github": {
            "configured": services.github_pat_configured(),
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
    if payload.source == "github":
        try:
            result = await services.import_github_module(
                payload.github_repo_url or payload.source_ref or "",
                payload.github_branch or "",
                payload.github_subpath,
            )
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        except ModuleSyncError as exc:
            return JSONResponse(status_code=409, content={"error": str(exc), "sync_state": exc.sync_state})
        except RuntimeError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        return {"id": result["id"], "status": result["status"]}

    result = await services.create_module_import(
        payload.source,
        payload.source_ref,
        payload.version_hash,
        github_repo_url=payload.github_repo_url,
        github_branch=payload.github_branch,
        github_subpath=payload.github_subpath,
        checkout_path=payload.checkout_path,
        current_commit_sha=payload.current_commit_sha,
        upstream_commit_sha=payload.upstream_commit_sha,
        sync_status=payload.sync_status,
        github_secrets_environment_name=payload.github_secrets_environment_name,
        environment_entries=payload.environment_entries,
    )
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


@app.patch("/modules/{module_id}")
async def update_module(module_id: str, request: Request, payload: ModuleMetadataUpdateRequest):
    services: AppServices = request.app.state.services
    current = await services.get_module(module_id)
    if current is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    if current.get("source") == "github":
        try:
            await services.ensure_module_mutation_allowed(module_id)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        except ModuleSyncError as exc:
            return JSONResponse(status_code=409, content={"error": str(exc), "sync_state": exc.sync_state})
    bundle_name = payload.bundle_name.strip() if isinstance(payload.bundle_name, str) else None
    bundle_version = payload.bundle_version.strip() if isinstance(payload.bundle_version, str) else None
    if payload.bundle_name is not None or payload.bundle_version is not None:
        await services.set_module_bundle_metadata(
            module_id,
            bundle_name,
            bundle_version,
        )
    if payload.github_secrets_environment_name is not None or payload.environment_entries is not None:
        try:
            await services.set_module_environment_config(
                module_id,
                payload.github_secrets_environment_name,
                payload.environment_entries or [],
            )
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        except RuntimeError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
    updated = await services.get_module(module_id)
    if updated is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return updated


@app.get("/modules/{module_id}/sync-status")
async def get_module_sync_status(module_id: str, request: Request):
    services: AppServices = request.app.state.services
    current = await services.get_module(module_id)
    if current is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return {
        "module_id": module_id,
        "sync_status": current.get("sync_status"),
        "current_commit_sha": current.get("current_commit_sha"),
        "upstream_commit_sha": current.get("upstream_commit_sha"),
        "github_branch": current.get("github_branch"),
        "github_repo_url": current.get("github_repo_url"),
        "github_subpath": current.get("github_subpath"),
        "last_sync_error": current.get("last_sync_error"),
        "last_synced_at": current.get("last_synced_at"),
    }


@app.post("/modules/{module_id}/sync-status")
async def refresh_module_sync_status(module_id: str, request: Request, payload: ModuleSyncRequest):
    services: AppServices = request.app.state.services
    try:
        return await services.refresh_module_sync_status(module_id)
    except ValueError as exc:
        status_code = 404 if str(exc) == "module not found" else 400
        return JSONResponse(status_code=status_code, content={"error": str(exc)})
    except ModuleSyncError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc), "sync_state": exc.sync_state})
    except RuntimeError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc)})


@app.post("/modules/{module_id}/sync")
async def sync_module(module_id: str, request: Request, payload: ModuleSyncRequest):
    services: AppServices = request.app.state.services
    try:
        return await services.sync_module(module_id)
    except ValueError as exc:
        status_code = 404 if str(exc) == "module not found" else 400
        return JSONResponse(status_code=status_code, content={"error": str(exc)})
    except ModuleSyncError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc), "sync_state": exc.sync_state})
    except RuntimeError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc)})


@app.get("/modules/{module_id}/revisions")
async def list_module_revisions(module_id: str, request: Request):
    services: AppServices = request.app.state.services
    current = await services.get_module(module_id)
    if current is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return await services.list_module_revisions(module_id)


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


@app.get("/modules/{module_id}/endpoints")
async def list_bundle_endpoints(module_id: str, request: Request):
    services: AppServices = request.app.state.services
    endpoints = await services.list_bundle_endpoints(module_id)
    if endpoints is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return endpoints


@app.post("/modules/{module_id}/endpoints")
async def create_bundle_endpoint(module_id: str, request: Request, payload: BundleEndpointCreateRequest):
    services: AppServices = request.app.state.services
    try:
        endpoint = await services.create_bundle_endpoint(module_id, payload.name, payload.lm_profile_id, payload.pinned_worker_count)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if endpoint is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return endpoint


@app.patch("/modules/{module_id}/endpoints/{endpoint_id}")
async def update_bundle_endpoint(module_id: str, endpoint_id: str, request: Request, payload: BundleEndpointUpdateRequest):
    services: AppServices = request.app.state.services
    if payload.name is None:
        return JSONResponse(status_code=400, content={"error": "name is required"})
    try:
        endpoint = await services.update_bundle_endpoint(
            module_id,
            endpoint_id,
            payload.name,
            payload.lm_profile_id,
            payload.pinned_worker_count,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if endpoint is None:
        return JSONResponse(status_code=404, content={"error": "endpoint not found"})
    return endpoint


@app.delete("/modules/{module_id}/endpoints/{endpoint_id}")
async def delete_bundle_endpoint(module_id: str, endpoint_id: str, request: Request):
    services: AppServices = request.app.state.services
    deleted = await services.delete_bundle_endpoint(module_id, endpoint_id)
    if not deleted:
        return JSONResponse(status_code=404, content={"error": "endpoint not found"})
    return {"id": endpoint_id, "deleted": True}


@app.post("/modules/{module_id}/endpoints/{endpoint_id}/regenerate-key")
async def regenerate_bundle_endpoint_key(module_id: str, endpoint_id: str, request: Request):
    services: AppServices = request.app.state.services
    endpoint = await services.regenerate_bundle_endpoint_key(module_id, endpoint_id)
    if endpoint is None:
        return JSONResponse(status_code=404, content={"error": "endpoint not found"})
    return endpoint


@app.get("/bundle-endpoints")
async def list_all_bundle_endpoints(request: Request):
    services: AppServices = request.app.state.services
    return await services.list_all_bundle_endpoints()


@app.post("/bundle-endpoints")
async def create_bundle_endpoint_global(request: Request, payload: BundleEndpointCreateRequest):
    services: AppServices = request.app.state.services
    try:
        endpoint = await services.create_bundle_endpoint_global(
            payload.name,
            payload.module_import_id or "",
            payload.lm_profile_id,
            payload.pinned_worker_count,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if endpoint is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    return endpoint


@app.get("/bundle-endpoints/{endpoint_id}")
async def get_bundle_endpoint(endpoint_id: str, request: Request):
    services: AppServices = request.app.state.services
    endpoint = await services.get_bundle_endpoint(endpoint_id)
    if endpoint is None:
        return JSONResponse(status_code=404, content={"error": "endpoint not found"})
    return endpoint


@app.patch("/bundle-endpoints/{endpoint_id}")
async def update_bundle_endpoint_global(endpoint_id: str, request: Request, payload: BundleEndpointUpdateRequest):
    services: AppServices = request.app.state.services
    try:
        endpoint = await services.update_bundle_endpoint_global(
            endpoint_id,
            name=payload.name,
            module_import_id=payload.module_import_id,
            lm_profile_id=payload.lm_profile_id,
            pinned_worker_count=payload.pinned_worker_count,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message == "module not found" else 400
        return JSONResponse(status_code=status_code, content={"error": message})
    if endpoint is None:
        return JSONResponse(status_code=404, content={"error": "endpoint not found"})
    return endpoint


@app.delete("/bundle-endpoints/{endpoint_id}")
async def delete_bundle_endpoint_global(endpoint_id: str, request: Request):
    services: AppServices = request.app.state.services
    deleted = await services.delete_bundle_endpoint_global(endpoint_id)
    if not deleted:
        return JSONResponse(status_code=404, content={"error": "endpoint not found"})
    return {"id": endpoint_id, "deleted": True}


@app.post("/bundle-endpoints/{endpoint_id}/regenerate-key")
async def regenerate_bundle_endpoint_key_global(endpoint_id: str, request: Request):
    services: AppServices = request.app.state.services
    endpoint = await services.regenerate_bundle_endpoint_key_global(endpoint_id)
    if endpoint is None:
        return JSONResponse(status_code=404, content={"error": "endpoint not found"})
    return endpoint


@app.post("/bundle-endpoints/{endpoint_id}/invoke")
async def invoke_bundle_endpoint(endpoint_id: str, request: Request):
    services: AppServices = request.app.state.services
    api_key = _read_endpoint_api_key(request)
    if not api_key:
        return JSONResponse(status_code=401, content={"error": "endpoint api key is required"})
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "request body must be a JSON object"})
    if not isinstance(payload, dict) or not payload:
        return JSONResponse(status_code=400, content={"error": "request body must be a non-empty JSON object"})
    endpoint = await services.authenticate_bundle_endpoint(endpoint_id, api_key)
    if endpoint is None:
        return JSONResponse(status_code=401, content={"error": "invalid endpoint id or api key"})
    if services.redis is None:
        return JSONResponse(status_code=503, content={"error": "queue not initialized"})
    redis_client = services.redis
    invocation_id = str(__import__("uuid").uuid4())
    channel = services._endpoint_invocation_channel(invocation_id)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel)
    try:
        await services.enqueue_endpoint_invocation(endpoint_id, payload, stream=False, invocation_id=invocation_id)
        deadline = asyncio.get_running_loop().time() + 300.0
        while asyncio.get_running_loop().time() < deadline:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is None:
                await asyncio.sleep(0.05)
                continue
            raw_data = message.get("data") if isinstance(message, dict) else None
            if isinstance(raw_data, bytes):
                raw_data = raw_data.decode("utf-8", errors="ignore")
            event_payload = json.loads(raw_data) if isinstance(raw_data, str) else None
            if not isinstance(event_payload, dict):
                continue
            event_name = str(event_payload.get("event") or "")
            event_body = event_payload.get("payload") if isinstance(event_payload.get("payload"), dict) else {}
            if event_name == "final":
                return event_body
            if event_name == "error":
                return JSONResponse(status_code=500, content=event_body)
        return JSONResponse(status_code=504, content={"error": "endpoint invocation timed out"})
    except RuntimeError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()


@app.post("/bundle-endpoints/{endpoint_id}/stream")
async def stream_bundle_endpoint(endpoint_id: str, request: Request):
    services: AppServices = request.app.state.services
    api_key = _read_endpoint_api_key(request)
    if not api_key:
        return JSONResponse(status_code=401, content={"error": "endpoint api key is required"})
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "request body must be a JSON object"})
    if not isinstance(payload, dict) or not payload:
        return JSONResponse(status_code=400, content={"error": "request body must be a non-empty JSON object"})
    endpoint = await services.authenticate_bundle_endpoint(endpoint_id, api_key)
    if endpoint is None:
        return JSONResponse(status_code=401, content={"error": "invalid endpoint id or api key"})
    if services.redis is None:
        return JSONResponse(status_code=503, content={"error": "queue not initialized"})
    redis_client = services.redis

    async def event_stream():
        invocation_id = str(__import__("uuid").uuid4())
        channel = services._endpoint_invocation_channel(invocation_id)
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel)
        try:
            await services.enqueue_endpoint_invocation(endpoint_id, payload, stream=True, invocation_id=invocation_id)
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    await asyncio.sleep(0.05)
                    continue
                raw_data = message.get("data") if isinstance(message, dict) else None
                if isinstance(raw_data, bytes):
                    raw_data = raw_data.decode("utf-8", errors="ignore")
                event_payload = json.loads(raw_data) if isinstance(raw_data, str) else None
                if not isinstance(event_payload, dict):
                    continue
                event_name = str(event_payload.get("event") or "")
                raw_event_body = event_payload.get("payload")
                event_body: dict[str, Any]
                if isinstance(raw_event_body, dict):
                    event_body = raw_event_body
                else:
                    event_body = {}
                yield _format_sse_event(event_name, event_body)
                if event_name in {"final", "error"}:
                    break
        except Exception as exc:
            yield _format_sse_event("error", {"error": str(exc)})
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/modules/{module_id}/validate")
async def validate_module(module_id: str, request: Request, payload: ValidateRequest):
    services: AppServices = request.app.state.services
    module_state = await services.resolve_module_execution_state(module_id, payload.bundle_path)
    if module_state is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    report = validate_bundle(module_state["bundle_path"])
    await services.set_module_bundle_metadata(
        module_id,
        report.metadata.get("name") if isinstance(report.metadata.get("name"), str) else None,
        report.metadata.get("version") if isinstance(report.metadata.get("version"), str) else None,
    )
    status = "passed" if report.passed else "failed"
    found = await services.set_validation_status(
        module_id,
        status,
        report.diagnostics,
        revision_id=module_state["bundle_revision_id"],
        commit_sha=module_state["bundle_commit_sha"],
        bundle_version=module_state["bundle_version"],
    )
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
    module_state = await services.resolve_module_execution_state(module_id, payload.bundle_path)
    if module_state is None:
        return JSONResponse(status_code=404, content={"error": "module not found"})
    found = await services.set_smoke_status(module_id, "running", [
        {
            "code": "smoke_test_started",
            "stage": "smoke_test",
            "message": "Smoke test execution started.",
            "bundle_path": module_state["bundle_path"],
        }
    ], revision_id=module_state["bundle_revision_id"], commit_sha=module_state["bundle_commit_sha"], bundle_version=module_state["bundle_version"])
    if not found:
        return JSONResponse(status_code=404, content={"error": "module not found"})

    try:
        await services.ensure_bundle_requirements_installed(module_state["bundle_path"])
        runtime_env = await services.get_module_runtime_environment(module_id)
        report = run_bundle_eval(
            bundle_path=module_state["bundle_path"],
            eval_inputs=payload.eval_inputs,
            num_threads=payload.num_threads,
            runtime_env=runtime_env,
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

    await services.set_smoke_status(
        module_id,
        final_status,
        diagnostics,
        revision_id=module_state["bundle_revision_id"],
        commit_sha=module_state["bundle_commit_sha"],
        bundle_version=module_state["bundle_version"],
    )
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
    module_id = str(job.get("module_import_id") or "").strip()
    if module_id:
        source_module = await services.get_module(module_id)
        if source_module is not None and source_module.get("source") == "github":
            try:
                await services.ensure_module_mutation_allowed(module_id)
            except ValueError as exc:
                return JSONResponse(status_code=400, content={"error": str(exc)})
            except ModuleSyncError as exc:
                return JSONResponse(status_code=409, content={"error": str(exc), "sync_state": exc.sync_state})
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


@app.post("/evaluation-datasets")
async def create_evaluation_dataset(request: Request, payload: EvaluationDatasetCreateRequest):
    services: AppServices = request.app.state.services
    try:
        result = await services.create_evaluation_dataset(
            project_id=payload.project_id,
            name=payload.name,
            description=payload.description,
            module_import_id=payload.module_import_id,
            records=payload.records,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    return result


@app.get("/evaluation-datasets")
async def list_evaluation_datasets(request: Request):
    services: AppServices = request.app.state.services
    return await services.list_evaluation_datasets()


@app.post("/evaluation-datasets/generate-rows")
async def generate_evaluation_dataset_rows(request: Request, payload: EvaluationPlanGenerateRowsRequest):
    services: AppServices = request.app.state.services
    try:
        return await services.generate_evaluation_rows(
            lm_profile_id=payload.lm_profile_id,
            module_import_id=payload.module_import_id,
            operator_prompt=payload.operator_prompt,
            existing_rows=payload.existing_rows,
            max_rows=payload.max_rows,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except RuntimeError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": f"unexpected generation failure: {exc}"})


@app.get("/evaluation-datasets/{dataset_id}")
async def get_evaluation_dataset(dataset_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.get_evaluation_dataset(dataset_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "evaluation dataset not found"})
    return result


@app.patch("/evaluation-datasets/{dataset_id}")
async def update_evaluation_dataset(dataset_id: str, request: Request, payload: EvaluationDatasetCreateRequest):
    services: AppServices = request.app.state.services
    try:
        result = await services.update_evaluation_dataset(
            dataset_id=dataset_id,
            project_id=payload.project_id,
            name=payload.name,
            description=payload.description,
            module_import_id=payload.module_import_id,
            records=payload.records,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if result is None:
        return JSONResponse(status_code=404, content={"error": "evaluation dataset not found"})
    return result


@app.post("/evaluation-datasets/{dataset_id}/duplicate")
async def duplicate_evaluation_dataset(dataset_id: str, request: Request, payload: EvaluationDatasetDuplicateRequest):
    services: AppServices = request.app.state.services
    try:
        result = await services.duplicate_evaluation_dataset(dataset_id=dataset_id, name=payload.name)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if result is None:
        return JSONResponse(status_code=404, content={"error": "evaluation dataset not found"})
    return result


@app.delete("/evaluation-datasets/{dataset_id}")
async def delete_evaluation_dataset(dataset_id: str, request: Request):
    services: AppServices = request.app.state.services
    try:
        deleted = await services.delete_evaluation_dataset(dataset_id)
    except ValueError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc)})
    if not deleted:
        return JSONResponse(status_code=404, content={"error": "evaluation dataset not found"})
    return {"id": dataset_id, "deleted": True}


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


@app.post("/agent-run-plans/{plan_id}/cancel")
async def cancel_agent_run_plan(plan_id: str, request: Request):
    services: AppServices = request.app.state.services
    result = await services.cancel_agent_run_plan(plan_id)
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
            dataset_id=payload.dataset_id,
            lm_profile_id=payload.lm_profile_id,
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
            dataset_id=payload.dataset_id,
            lm_profile_id=payload.lm_profile_id,
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
