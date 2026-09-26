import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import endpoint_worker
from app.config import Settings
from app.revision_image_builder import BUNDLE_IMAGE_PATH, _generated_entrypoint
from app.services import AppServices


class _WorkerServices:
    def __init__(self):
        self.postgres_pool = object()
        self.heartbeats = []
        self.events = []
        self.invocations = []

    async def heartbeat_endpoint_worker(self, worker_id, **payload):
        self.heartbeats.append((worker_id, payload))
        return {"worker_id": worker_id}

    async def publish_endpoint_invocation_event(self, invocation_id, event, payload):
        self.events.append((invocation_id, event, payload))

    async def run_endpoint_invocation_job(self, *args, **kwargs):
        self.invocations.append((args, kwargs))

    async def get_endpoint_deployment(self, endpoint_id):
        raise AssertionError(
            f"managed readiness must not resolve deployment state: {endpoint_id}"
        )

    async def get_bundle_endpoint(self, endpoint_id):
        raise AssertionError(
            f"managed readiness must not resolve mutable endpoint state: {endpoint_id}"
        )

    async def resolve_module_execution_state(self, module_id):
        raise AssertionError(
            f"managed readiness must not resolve mutable checkout state: {module_id}"
        )

    async def ensure_bundle_requirements_installed(self, bundle_path):
        raise AssertionError(
            f"managed readiness must not install dependencies: {bundle_path}"
        )


def _managed_runtime_identity(bundle_path: str = BUNDLE_IMAGE_PATH):
    boot_identity = {
        "execution_mode": "managed_image",
        "endpoint_id": "endpoint-1",
        "build_id": "build-1",
        "revision_id": "revision-1",
        "bundle_path": bundle_path,
        "worker_id": "worker-1",
        "deployment_id": "deployment-1",
        "slot": 0,
        "rollout_generation": 2,
    }
    return {
        "runtime_instance_id": "runtime-1",
        "hostname": "image-worker",
        "pid": 42,
        "boot_identity": boot_identity,
        "runtime_metadata": {
            "execution_mode": "managed_image",
            "endpoint_id": "endpoint-1",
            "desired_build_id": "build-1",
            "desired_revision_id": "revision-1",
            "baked_build_id": "build-1",
            "baked_revision_id": "revision-1",
            "bundle_path": bundle_path,
            "worker_id": "worker-1",
            "endpoint_deployment_id": "deployment-1",
            "endpoint_slot": 0,
            "endpoint_rollout_generation": 2,
        },
    }


def test_managed_boot_identity_requires_the_fixed_existing_baked_bundle(
    monkeypatch, tmp_path
):
    baked_bundle = tmp_path / "bundle"
    baked_bundle.mkdir()
    monkeypatch.setattr(endpoint_worker, "BUNDLE_IMAGE_PATH", str(baked_bundle))
    identity = endpoint_worker.load_endpoint_worker_boot_identity(
        {
            "DSPY_TRAINER_ENDPOINT_WORKER_MODE": "managed_image",
            "DSPY_TRAINER_ENDPOINT_ID": "endpoint-1",
            "DSPY_TRAINER_BAKED_BUILD_ID": "build-1",
            "DSPY_TRAINER_BAKED_REVISION_ID": "revision-1",
            "DSPY_TRAINER_BUNDLE_PATH": str(baked_bundle),
            "DSPY_TRAINER_WORKER_ID": "worker-1",
            "DSPY_TRAINER_ENDPOINT_DEPLOYMENT_ID": "deployment-1",
            "DSPY_TRAINER_ENDPOINT_SLOT": "0",
            "DSPY_TRAINER_ENDPOINT_ROLLOUT_GENERATION": "2",
        }
    )
    assert identity == {
        "execution_mode": "managed_image",
        "endpoint_id": "endpoint-1",
        "build_id": "build-1",
        "revision_id": "revision-1",
        "bundle_path": str(baked_bundle),
        "worker_id": "worker-1",
        "deployment_id": "deployment-1",
        "slot": 0,
        "rollout_generation": 2,
    }

    with pytest.raises(RuntimeError, match="bundle path must be"):
        endpoint_worker.load_endpoint_worker_boot_identity(
            {
                "DSPY_TRAINER_ENDPOINT_WORKER_MODE": "managed_image",
                "DSPY_TRAINER_ENDPOINT_ID": "endpoint-1",
                "DSPY_TRAINER_BAKED_BUILD_ID": "build-1",
                "DSPY_TRAINER_BAKED_REVISION_ID": "revision-1",
                "DSPY_TRAINER_BUNDLE_PATH": str(tmp_path / "checkout"),
                "DSPY_TRAINER_WORKER_ID": "worker-1",
                "DSPY_TRAINER_ENDPOINT_DEPLOYMENT_ID": "deployment-1",
                "DSPY_TRAINER_ENDPOINT_SLOT": "0",
                "DSPY_TRAINER_ENDPOINT_ROLLOUT_GENERATION": "2",
            }
        )


def test_generated_managed_entrypoint_strips_broad_host_and_build_environment():
    entrypoint = _generated_entrypoint(build_id="build-1", revision_id="revision-1")
    assert "exec env -i" in entrypoint
    assert "DSPY_TRAINER_BAKED_BUILD_ID=build-1" in entrypoint
    assert "DSPY_TRAINER_BAKED_REVISION_ID=revision-1" in entrypoint
    assert 'BAKED_BUILD_ID="$DSPY_TRAINER_BAKED_BUILD_ID"' not in entrypoint
    assert "DSPY_TRAINER_POSTGRES_DSN" in entrypoint
    assert "DSPY_TRAINER_REDIS_URL" in entrypoint
    assert "DSPY_TRAINER_MLFLOW_TRACKING_URI" in entrypoint
    assert "DSPY_TRAINER_MODULE_ENV_ENCRYPTION_KEY" in entrypoint
    assert "GITHUB_PAT" not in entrypoint
    assert "CHECKOUT_ROOT" not in entrypoint
    assert "DEPLOYER_" not in entrypoint


def test_managed_assignment_becomes_ready_without_checkout_resolution_or_install():
    services = _WorkerServices()
    runtime_identity = _managed_runtime_identity()
    target = asyncio.run(
        endpoint_worker.ensure_endpoint_assignment_ready(
            services,
            "worker-1",
            {
                "endpoint_id": "endpoint-1",
                "execution_mode": "managed_image",
                "build_id": "build-1",
                "revision_id": "revision-1",
                "bundle_path": BUNDLE_IMAGE_PATH,
            },
            runtime_identity=runtime_identity,
        )
    )
    assert target == {
        "execution_mode": "managed_image",
        "endpoint_id": "endpoint-1",
        "build_id": "build-1",
        "revision_id": "revision-1",
        "bundle_path": BUNDLE_IMAGE_PATH,
    }
    heartbeat = services.heartbeats[-1][1]
    assert heartbeat["status"] == "listening"
    assert heartbeat["runtime_metadata"]["desired_build_id"] == "build-1"
    assert heartbeat["runtime_metadata"]["warmed_build_id"] == "build-1"
    assert heartbeat["runtime_metadata"]["desired_revision_id"] == "revision-1"
    assert heartbeat["runtime_metadata"]["warmed_revision_id"] == "revision-1"


def test_worker_rejects_job_whose_pinned_provenance_does_not_match_assignment():
    services = _WorkerServices()
    asyncio.run(
        endpoint_worker.process_endpoint_job(
            services,
            json.dumps(
                {
                    "type": "endpoint_invocation",
                    "invocation_id": "invocation-1",
                    "endpoint_id": "endpoint-1",
                    "execution_mode": "managed_image",
                    "build_id": "newer-build",
                    "revision_id": "newer-revision",
                    "bundle_path": BUNDLE_IMAGE_PATH,
                    "input_payload": {"question": "ignored"},
                }
            ),
            "worker-1",
            {
                "endpoint_id": "endpoint-1",
                "execution_mode": "managed_image",
                "build_id": "build-1",
                "revision_id": "revision-1",
                "bundle_path": BUNDLE_IMAGE_PATH,
            },
            runtime_identity=_managed_runtime_identity(),
        )
    )
    assert services.invocations == []
    assert services.events == [
        (
            "invocation-1",
            "error",
            {
                "error": "worker assignment mismatch",
                "code": "worker_assignment_mismatch",
            },
        )
    ]


class _Redis:
    def __init__(self):
        self.commands = []

    async def execute_command(self, *args):
        self.commands.append(args)


def test_managed_routing_queues_pinned_build_and_revision():
    services = AppServices(
        Settings(
            postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"
        )
    )
    services.postgres_pool = object()
    services.redis = _Redis()

    async def no_reconcile():
        return None

    async def deployment(endpoint_id):
        return {
            "endpoint_id": endpoint_id,
            "phase": "ready",
            "active_build_id": "build-1",
            "active_revision_id": "revision-1",
            "target_build_id": None,
            "target_revision_id": None,
        }

    async def workers():
        return {
            "items": [
                {
                    "status": "listening",
                    "is_live": True,
                    "endpoint_id": "endpoint-1",
                    "assigned_endpoint_id": "endpoint-1",
                    "execution_mode": "managed_image",
                    "desired_build_id": "build-1",
                    "warmed_build_id": "build-1",
                    "desired_revision_id": "revision-1",
                    "warmed_revision_id": "revision-1",
                    "bundle_path": BUNDLE_IMAGE_PATH,
                },
                {
                    "status": "listening",
                    "is_live": True,
                    "endpoint_id": "endpoint-1",
                    "assigned_endpoint_id": "endpoint-1",
                    "execution_mode": "managed_image",
                    "desired_build_id": "build-2",
                    "warmed_build_id": "build-2",
                    "desired_revision_id": "revision-2",
                    "warmed_revision_id": "revision-2",
                    "bundle_path": BUNDLE_IMAGE_PATH,
                },
            ]
        }

    services.reconcile_endpoint_worker_assignments = no_reconcile  # type: ignore[method-assign]
    services.get_endpoint_deployment = deployment  # type: ignore[method-assign]
    services.list_endpoint_workers = workers  # type: ignore[method-assign]

    invocation_id = asyncio.run(
        services.enqueue_endpoint_invocation(
            "endpoint-1",
            {"question": "which revision?"},
            stream=True,
            invocation_id="invocation-1",
        )
    )

    assert invocation_id == "invocation-1"
    command, queue_name, raw_payload = services.redis.commands[0]
    assert command == "LPUSH"
    assert (
        queue_name
        == "dspy-trainer:endpoint-queues:endpoint-1:build:build-1:revision:revision-1"
    )
    payload = json.loads(raw_payload)
    assert payload["build_id"] == "build-1"
    assert payload["revision_id"] == "revision-1"
    assert payload["bundle_path"] == BUNDLE_IMAGE_PATH


def test_managed_identity_validation_matches_baked_labels_to_database_assignment():
    services = AppServices(
        Settings(
            postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"
        )
    )
    services.postgres_pool = object()

    async def deployment(endpoint_id):
        return {
            "endpoint_id": endpoint_id,
            "phase": "rolling",
            "active_build_id": "build-old",
            "active_revision_id": "revision-old",
            "target_build_id": "build-1",
            "target_revision_id": "revision-1",
            "previous_build_id": None,
            "previous_revision_id": None,
        }

    async def build(build_id):
        return {
            "id": build_id,
            "revision_id": "revision-1",
            "status": "ready",
            "image_id": "sha256:image",
        }

    services.get_endpoint_deployment = deployment  # type: ignore[method-assign]
    services.get_revision_image_build = build  # type: ignore[method-assign]
    metadata = {
        "execution_mode": "managed_image",
        "endpoint_id": "endpoint-1",
        "desired_build_id": "build-1",
        "desired_revision_id": "revision-1",
        "baked_build_id": "build-1",
        "baked_revision_id": "revision-1",
        "bundle_path": BUNDLE_IMAGE_PATH,
    }
    identity = asyncio.run(services.validate_managed_endpoint_worker_identity(metadata))
    assert identity["build_id"] == "build-1"
    assert identity["revision_id"] == "revision-1"

    with pytest.raises(ValueError, match="does not match baked image identity"):
        asyncio.run(
            services.validate_managed_endpoint_worker_identity(
                {**metadata, "baked_build_id": "different-build"}
            )
        )


def test_managed_invocation_trace_records_actual_pinned_image_provenance(monkeypatch):
    services = AppServices(
        Settings(
            postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"
        )
    )
    captured = {}
    events = []

    async def endpoint(endpoint_id):
        return {
            "id": endpoint_id,
            "module_import_id": "module-1",
            "lm_profile_id": None,
            "name": "Managed",
        }

    async def registration(worker_id):
        assert worker_id == "worker-1"
        return {
            "is_live": True,
            "runtime_metadata": {
                "execution_mode": "managed_image",
                "endpoint_id": "endpoint-1",
                "worker_id": "worker-1",
                "endpoint_deployment_id": "deployment-1",
                "endpoint_slot": 0,
                "endpoint_rollout_generation": 1,
                "desired_build_id": "build-1",
                "desired_revision_id": "revision-1",
                "baked_build_id": "build-1",
                "baked_revision_id": "revision-1",
                "bundle_path": BUNDLE_IMAGE_PATH,
            },
        }

    async def validate(metadata, *, worker_id=None):
        return {
            "endpoint_id": metadata["endpoint_id"],
            "build_id": metadata["desired_build_id"],
            "revision_id": metadata["desired_revision_id"],
            "baked_build_id": metadata["baked_build_id"],
            "baked_revision_id": metadata["baked_revision_id"],
            "bundle_path": metadata["bundle_path"],
        }

    async def runtime_environment(module_id):
        assert module_id == "module-1"
        return {}

    async def publish(invocation_id, event, payload):
        events.append((invocation_id, event, payload))

    async def forbidden(*args, **kwargs):
        raise AssertionError(
            "managed invocation must not resolve a checkout or install dependencies"
        )

    def invoke_bundle(bundle_path, input_payload, lm_profile, runtime_env):
        assert bundle_path == BUNDLE_IMAGE_PATH
        return {"answer": input_payload["question"]}

    def run_with_mlflow(operation, *, tracking_uri, input_payload, attributes):
        captured.update(attributes)
        return operation(), "trace-1"

    services.get_bundle_endpoint = endpoint  # type: ignore[method-assign]
    services._get_endpoint_worker_registration = registration  # type: ignore[method-assign]
    services.validate_managed_endpoint_worker_identity = validate  # type: ignore[method-assign]
    services.get_module_runtime_environment = runtime_environment  # type: ignore[method-assign]
    services.publish_endpoint_invocation_event = publish  # type: ignore[method-assign]
    services.resolve_module_execution_state = forbidden  # type: ignore[method-assign]
    services.ensure_bundle_requirements_installed = forbidden  # type: ignore[method-assign]
    monkeypatch.setattr("app.executor.module_runner.invoke_bundle", invoke_bundle)
    monkeypatch.setattr(
        "app.services._run_endpoint_invocation_with_mlflow", run_with_mlflow
    )

    asyncio.run(
        services.run_endpoint_invocation_job(
            "invocation-1",
            "endpoint-1",
            {"question": "pinned"},
            "worker-1",
            stream=False,
            execution_mode="managed_image",
            build_id="build-1",
            revision_id="revision-1",
            bundle_path=BUNDLE_IMAGE_PATH,
        )
    )

    assert captured["revision_image_build_id"] == "build-1"
    assert captured["bundle_revision_id"] == "revision-1"
    assert captured["execution_mode"] == "managed_image"
    assert events == [("invocation-1", "final", {"answer": "pinned"})]
