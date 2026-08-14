import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cryptography.fernet import Fernet
import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bundle_preparation import activate_bundle_preparation, bundle_preparation_cache_root, inspect_bundle_preparation
from app.config import Settings
from app.services import AppServices, _classify_sync_status, _json_ready


class _FakeAsyncProcess:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode: int | None = None
        self._final_returncode = returncode
        self._stdout = stdout.encode("utf-8")
        self._stderr = stderr.encode("utf-8")

    async def wait(self):
        self.returncode = self._final_returncode
        return self.returncode

    async def communicate(self):
        if self.returncode is None:
            self.returncode = self._final_returncode
        return self._stdout, self._stderr

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class _Conn:
    def __init__(self, state):
        self.state = state

    async def fetchrow(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if normalized == "select source, source_ref, checkout_path, github_subpath, current_commit_sha from module_imports where id = $1":
            module = self.state.get(str(params[0]))
            if module is None:
                return None
            return {
                "source": module.get("source", "upload"),
                "source_ref": module["source_ref"],
                "checkout_path": module.get("checkout_path"),
                "github_subpath": module.get("github_subpath"),
                "current_commit_sha": module.get("current_commit_sha"),
            }
        return None

    async def execute(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if normalized.startswith("update module_imports set bundle_name = coalesce($2, bundle_name), bundle_version = coalesce($3, bundle_version), updated_at = now() where id = $1"):
            module = self.state.get(str(params[0]))
            if module is not None:
                module["bundle_name"] = params[1]
                module["bundle_version"] = params[2]
        elif normalized.startswith("insert into bundle_revisions"):
            self.state.setdefault("_revisions", []).append(
                {
                    "id": params[0],
                    "module_import_id": params[1],
                    "commit_sha": params[2],
                    "checkout_path": params[3],
                    "bundle_name": params[4],
                    "bundle_version": params[5],
                    "source_event": params[6],
                }
            )
        elif normalized.startswith("update module_imports set current_revision_id = $2, updated_at = now() where id = $1"):
            module = self.state.get(str(params[0]))
            if module is not None:
                module["current_revision_id"] = params[1]
        return "UPDATE 1"


class _Acquire:
    def __init__(self, state):
        self.conn = _Conn(state)

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, state):
        self.state = state

    def acquire(self):
        return _Acquire(self.state)


class _RedisPublisher:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    async def publish(self, channel, payload):
        self.messages.append((channel, payload))


class _EndpointConn:
    def __init__(self, state):
        self.state = state

    async def fetchrow(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if normalized.startswith("update bundle_endpoints set name = $2, module_import_id = $3"):
            endpoint = self.state["endpoints"].get(str(params[0]))
            if endpoint is None:
                return None
            endpoint["name"] = params[1]
            endpoint["module_import_id"] = params[2]
            endpoint["lm_profile_id"] = params[3]
            endpoint["pinned_worker_count"] = params[4]
            if params[5]:
                endpoint["prepared_revision_id"] = None
                endpoint["prepared_digest"] = None
                endpoint["prepared_image_ref"] = None
                endpoint["prepared_image_digest"] = None
                endpoint["prepared_at"] = None
                endpoint["deployed_revision_id"] = None
                endpoint["deployed_digest"] = None
                endpoint["deployed_image_ref"] = None
                endpoint["deployed_image_digest"] = None
                endpoint["deployed_at"] = None
                endpoint["restart_generation"] = 0
                endpoint["rollout_state"] = {}
                endpoint["rollout_operations"] = []
                endpoint["rollout_events"] = []
            endpoint["updated_at"] = params[6]
            return {
                "id": endpoint["id"],
                "module_import_id": endpoint["module_import_id"],
                "lm_profile_id": endpoint.get("lm_profile_id"),
                "pinned_worker_count": endpoint["pinned_worker_count"],
                "name": endpoint["name"],
                "key_preview": endpoint.get("key_preview"),
                "created_at": endpoint.get("created_at"),
                "updated_at": endpoint.get("updated_at"),
            }
        return None


class _EndpointAcquire:
    def __init__(self, state):
        self.conn = _EndpointConn(state)

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _EndpointPool:
    def __init__(self, state):
        self.state = state

    def acquire(self):
        return _EndpointAcquire(self.state)


class _RolloutConn:
    def __init__(self, state):
        self.state = state

    async def execute(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        endpoint = self.state["endpoints"].get(str(params[0]))
        if endpoint is None:
            return "UPDATE 0"
        if normalized.startswith("update bundle_endpoints set prepared_revision_id = $2"):
            endpoint["prepared_revision_id"] = params[1]
            endpoint["prepared_digest"] = params[2]
            endpoint["prepared_image_ref"] = params[3]
            endpoint["prepared_image_digest"] = params[4]
            endpoint["prepared_at"] = params[5]
            endpoint["updated_at"] = params[5]
            return "UPDATE 1"
        if normalized.startswith("update bundle_endpoints set deployed_revision_id = $2"):
            endpoint["deployed_revision_id"] = params[1]
            endpoint["deployed_digest"] = params[2]
            endpoint["deployed_image_ref"] = params[3]
            endpoint["deployed_image_digest"] = params[4]
            endpoint["deployed_at"] = params[5]
            endpoint["updated_at"] = params[5]
            return "UPDATE 1"
        if normalized.startswith("update bundle_endpoints set restart_generation = $2"):
            endpoint["restart_generation"] = params[1]
            endpoint["rollout_state"] = params[2]
            endpoint["rollout_operations"] = params[3]
            endpoint["rollout_events"] = params[4]
            endpoint["updated_at"] = params[5]
            return "UPDATE 1"
        return "UPDATE 0"


class _RolloutAcquire:
    def __init__(self, state):
        self.conn = _RolloutConn(state)

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _RolloutPool:
    def __init__(self, state):
        self.state = state

    def acquire(self):
        return _RolloutAcquire(self.state)


def _serialize_endpoint_rollout_payload(state, endpoint_id):
    endpoint = state["endpoints"][endpoint_id]
    current_module_revision_id = state["modules"].get(endpoint["module_import_id"], {}).get("current_revision_id")
    return AppServices._build_bundle_endpoint_payload(
        {
            "id": endpoint["id"],
            "module_import_id": endpoint["module_import_id"],
            "lm_profile_id": endpoint.get("lm_profile_id"),
            "pinned_worker_count": endpoint.get("pinned_worker_count", 1),
            "name": endpoint["name"],
            "key_preview": endpoint.get("key_preview", "abc123"),
            "prepared_revision_id": endpoint.get("prepared_revision_id"),
            "prepared_digest": endpoint.get("prepared_digest"),
            "prepared_image_ref": endpoint.get("prepared_image_ref"),
            "prepared_image_digest": endpoint.get("prepared_image_digest"),
            "prepared_at": endpoint.get("prepared_at"),
            "deployed_revision_id": endpoint.get("deployed_revision_id"),
            "deployed_digest": endpoint.get("deployed_digest"),
            "deployed_image_ref": endpoint.get("deployed_image_ref"),
            "deployed_image_digest": endpoint.get("deployed_image_digest"),
            "deployed_at": endpoint.get("deployed_at"),
            "restart_generation": endpoint.get("restart_generation", 0),
            "rollout_state": endpoint.get("rollout_state", {}),
            "rollout_operations": endpoint.get("rollout_operations", []),
            "rollout_events": endpoint.get("rollout_events", []),
            "current_module_revision_id": current_module_revision_id,
            "current_module_commit_sha": state["modules"].get(endpoint["module_import_id"], {}).get("current_commit_sha"),
            "current_module_bundle_version": state["modules"].get(endpoint["module_import_id"], {}).get("bundle_version"),
            "created_at": endpoint.get("created_at"),
            "updated_at": endpoint.get("updated_at"),
        }
    )


def test_set_module_bundle_metadata_updates_saved_bundle_toml(tmp_path):
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    bundle_toml = bundle_root / "bundle.toml"
    bundle_toml.write_text(
        'name = "before-name"\nversion = "0.1.0"\nscore_pass_threshold = 0.8\n',
        encoding="utf-8",
    )
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    state: dict[str, Any] = {
        "mod-1": {
            "source": "upload",
            "source_ref": str(bundle_root),
            "checkout_path": str(bundle_root),
            "current_commit_sha": "abc123",
            "bundle_name": "before-name",
            "bundle_version": "0.1.0",
        }
    }
    setattr(services, "postgres_pool", _Pool(state))

    asyncio.run(services.set_module_bundle_metadata("mod-1", "after-name", "2.0.0"))

    updated = bundle_toml.read_text(encoding="utf-8")
    assert 'name = "after-name"' in updated
    assert 'version = "2.0.0"' in updated
    assert state["mod-1"]["bundle_name"] == "after-name"
    assert state["mod-1"]["bundle_version"] == "2.0.0"
    assert state["mod-1"]["current_revision_id"]
    revisions = state["_revisions"]
    last_revision = revisions[-1]
    assert last_revision["commit_sha"] == "abc123"
    assert last_revision["bundle_version"] == "2.0.0"


def test_update_bundle_endpoint_global_clears_revision_metadata_when_module_changes(monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    state = {
        "modules": {
            "mod-1": {"id": "mod-1", "current_revision_id": "rev-old"},
            "mod-2": {"id": "mod-2", "current_revision_id": "rev-new"},
        },
        "endpoints": {
            "endpoint-1": {
                "id": "endpoint-1",
                "module_import_id": "mod-1",
                "lm_profile_id": "lm-1",
                "pinned_worker_count": 2,
                "name": "Customer API",
                "key_preview": "abc123",
                "prepared_revision_id": "rev-old",
                "prepared_digest": "digest-rev-old",
                "prepared_at": "2025-01-01T00:00:00+00:00",
                "deployed_revision_id": "rev-old",
                "deployed_at": "2025-01-01T00:05:00+00:00",
                "created_at": None,
                "updated_at": None,
                "current_module_revision_id": "rev-old",
            }
        },
    }
    services.postgres_pool = _EndpointPool(state)

    async def fake_get_bundle_endpoint(endpoint_id):
        endpoint = state["endpoints"].get(endpoint_id)
        if endpoint is None:
            return None
        current_module_revision_id = state["modules"].get(endpoint["module_import_id"], {}).get("current_revision_id")
        prepared_revision_id = endpoint.get("prepared_revision_id")
        deployed_revision_id = endpoint.get("deployed_revision_id")
        return {
            **endpoint,
            "current_module_revision_id": current_module_revision_id,
            "prepared_revision_is_current": bool(prepared_revision_id and prepared_revision_id == current_module_revision_id),
            "deployed_revision_is_current": bool(deployed_revision_id and deployed_revision_id == current_module_revision_id),
        }

    async def fake_get_module(module_id):
        return state["modules"].get(module_id)

    async def fake_get_lm_profile(profile_id):
        return {"id": profile_id} if profile_id == "lm-2" else None

    async def fake_reconcile_endpoint_worker_assignments():
        return None

    monkeypatch.setattr(services, "get_bundle_endpoint", fake_get_bundle_endpoint)
    monkeypatch.setattr(services, "get_module", fake_get_module)
    monkeypatch.setattr(services, "get_lm_profile", fake_get_lm_profile)
    monkeypatch.setattr(services, "reconcile_endpoint_worker_assignments", fake_reconcile_endpoint_worker_assignments)

    payload = asyncio.run(
        services.update_bundle_endpoint_global(
            "endpoint-1",
            name="Customer Stream",
            module_import_id="mod-2",
            lm_profile_id="lm-2",
            pinned_worker_count=4,
        )
    )

    assert payload is not None
    assert payload["module_import_id"] == "mod-2"
    assert payload["current_module_revision_id"] == "rev-new"
    assert payload["prepared_revision_id"] is None
    assert payload["deployed_revision_id"] is None
    assert payload["prepared_revision_is_current"] is False
    assert payload["deployed_revision_is_current"] is False
    assert state["endpoints"]["endpoint-1"]["prepared_digest"] is None
    assert state["endpoints"]["endpoint-1"]["prepared_image_ref"] is None
    assert state["endpoints"]["endpoint-1"]["deployed_digest"] is None
    assert state["endpoints"]["endpoint-1"]["rollout_operations"] == []
    assert state["endpoints"]["endpoint-1"]["deployed_at"] is None


def test_build_bundle_endpoint_payload_includes_rollout_state_metadata():
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    payload = services._build_bundle_endpoint_payload(
        {
            "id": "endpoint-1",
            "module_import_id": "mod-1",
            "lm_profile_id": "lm-1",
            "pinned_worker_count": 2,
            "name": "Customer API",
            "key_preview": "abc123",
            "prepared_revision_id": "rev-2",
            "prepared_digest": "bundle-digest-2",
            "prepared_image_ref": "registry.test/demo:rev-2",
            "prepared_image_digest": "sha256:prepared",
            "prepared_at": None,
            "deployed_revision_id": "rev-1",
            "deployed_digest": "bundle-digest-1",
            "deployed_image_ref": "registry.test/demo:rev-1",
            "deployed_image_digest": "sha256:deployed",
            "deployed_at": None,
            "restart_generation": 3,
            "rollout_state": '{"status":"completed","last_action":"deploy"}',
            "rollout_operations": '[{"action":"rebuild"},{"action":"deploy"}]',
            "rollout_events": [{"action": "restart-runtime", "kind": "operation-completed"}],
            "current_module_revision_id": "rev-2",
            "current_module_commit_sha": "abc123",
            "current_module_bundle_version": "0.1.0",
            "created_at": None,
            "updated_at": None,
        }
    )

    assert payload["prepared_image_ref"] == "registry.test/demo:rev-2"
    assert payload["deployed_image_digest"] == "sha256:deployed"
    assert payload["restart_generation"] == 3
    assert payload["rollout_state"]["last_action"] == "deploy"
    assert [item["action"] for item in payload["rollout_operations"]] == ["rebuild", "deploy"]
    assert payload["rollout_events"][0]["action"] == "restart-runtime"


def test_bundle_endpoint_rollout_state_persists_rebuild_deploy_and_restart(monkeypatch, tmp_path):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    (bundle_root / "requirements.txt").write_text("dspy==2.6.27\n", encoding="utf-8")
    state = {
        "modules": {
            "mod-1": {
                "id": "mod-1",
                "current_revision_id": "rev-1",
                "current_commit_sha": "abc123",
                "bundle_version": "0.1.0",
            }
        },
        "endpoints": {
            "endpoint-1": {
                "id": "endpoint-1",
                "module_import_id": "mod-1",
                "lm_profile_id": None,
                "pinned_worker_count": 1,
                "name": "Customer API",
                "key_preview": "abc123",
                "prepared_revision_id": None,
                "prepared_digest": None,
                "prepared_image_ref": None,
                "prepared_image_digest": None,
                "prepared_at": None,
                "deployed_revision_id": None,
                "deployed_digest": None,
                "deployed_image_ref": None,
                "deployed_image_digest": None,
                "deployed_at": None,
                "restart_generation": 0,
                "rollout_state": {},
                "rollout_operations": [],
                "rollout_events": [],
                "created_at": None,
                "updated_at": None,
            }
        },
    }
    services.postgres_pool = _RolloutPool(state)

    async def fake_get_bundle_endpoint(endpoint_id):
        return _serialize_endpoint_rollout_payload(state, endpoint_id)

    async def fake_resolve_module_execution_state(module_id):
        return {
            "module_id": module_id,
            "bundle_path": str(bundle_root),
            "bundle_revision_id": "rev-1",
            "bundle_commit_sha": "abc123",
            "bundle_version": "0.1.0",
            "bundle_name": "demo-bundle",
        }

    async def fake_ensure_bundle_requirements_installed(bundle_path):
        assert bundle_path == str(bundle_root)
        return None

    monkeypatch.setattr(services, "get_bundle_endpoint", fake_get_bundle_endpoint)
    monkeypatch.setattr(services, "resolve_module_execution_state", fake_resolve_module_execution_state)
    monkeypatch.setattr(services, "ensure_bundle_requirements_installed", fake_ensure_bundle_requirements_installed)

    async def fake_build_prepared_bundle_image(**kwargs):
        assert kwargs["endpoint_id"] == "endpoint-1"
        assert kwargs["revision_id"] == "rev-1"
        assert kwargs["existing_image_ref"] is None
        return {
            "image_ref": "dspy-trainer/prepared-endpoints/endpoint-1:rev-rev-1-prep-bundle-digest",
            "image_digest": "sha256:prepared",
            "reused": False,
        }

    monkeypatch.setattr(services, "_build_prepared_bundle_image", fake_build_prepared_bundle_image)

    rebuilt = asyncio.run(services.rebuild_bundle_endpoint("endpoint-1"))
    deployed = asyncio.run(services.deploy_bundle_endpoint("endpoint-1"))
    restarted = asyncio.run(services.restart_bundle_endpoint_runtime("endpoint-1"))

    assert rebuilt is not None
    assert rebuilt["prepared_revision_id"] == "rev-1"
    assert rebuilt["prepared_digest"]
    assert rebuilt["prepared_image_ref"] == "dspy-trainer/prepared-endpoints/endpoint-1:rev-rev-1-prep-bundle-digest"
    assert rebuilt["prepared_image_digest"] == "sha256:prepared"
    assert rebuilt["rollout_operations"][0]["action"] == "rebuild"
    assert rebuilt["rollout_operations"][0]["metadata"]["prepared_image_reused"] is False

    assert deployed is not None
    assert deployed["deployed_revision_id"] == "rev-1"
    assert deployed["deployed_digest"] == rebuilt["prepared_digest"]
    assert deployed["deployed_image_ref"] == rebuilt["prepared_image_ref"]
    assert deployed["deployed_image_digest"] == rebuilt["prepared_image_digest"]
    assert deployed["rollout_operations"][1]["action"] == "deploy"

    assert restarted is not None
    assert restarted["restart_generation"] == 1
    assert restarted["rollout_state"]["last_action"] == "restart-runtime"
    assert [item["action"] for item in restarted["rollout_operations"]] == ["rebuild", "deploy", "restart-runtime"]
    assert [item["action"] for item in restarted["rollout_events"]] == ["rebuild", "deploy", "restart-runtime"]


def test_deploy_bundle_endpoint_requires_valid_prepared_image_metadata(tmp_path, monkeypatch):
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    (bundle_root / "bundle.py").write_text("def run(x):\n    return x\n", encoding="utf-8")
    (bundle_root / "bundle.toml").write_text('name = "demo-bundle"\nversion = "0.1.0"\n', encoding="utf-8")

    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    current_digest = inspect_bundle_preparation(str(bundle_root)).digest
    state = {
        "modules": {
            "mod-1": {"id": "mod-1", "current_revision_id": "rev-1", "current_commit_sha": "abc123", "bundle_version": "0.1.0"},
        },
        "endpoints": {
            "endpoint-1": {
                "id": "endpoint-1",
                "module_import_id": "mod-1",
                "name": "Customer API",
                "prepared_revision_id": "rev-1",
                "prepared_digest": current_digest,
                "prepared_image_ref": None,
                "prepared_image_digest": "sha256:stale",
                "deployed_revision_id": None,
                "deployed_digest": None,
                "deployed_image_ref": None,
                "deployed_image_digest": None,
                "rollout_state": {},
                "rollout_operations": [],
                "rollout_events": [],
            }
        },
    }
    services.postgres_pool = _RolloutPool(state)

    async def fake_get_bundle_endpoint(endpoint_id):
        return _serialize_endpoint_rollout_payload(state, endpoint_id)

    async def fake_resolve_module_execution_state(module_id):
        assert module_id == "mod-1"
        return {
            "module_id": module_id,
            "bundle_path": str(bundle_root),
            "bundle_revision_id": "rev-1",
            "bundle_commit_sha": "abc123",
            "bundle_version": "0.1.0",
            "bundle_name": "demo-bundle",
        }

    monkeypatch.setattr(services, "get_bundle_endpoint", fake_get_bundle_endpoint)
    monkeypatch.setattr(services, "resolve_module_execution_state", fake_resolve_module_execution_state)

    with pytest.raises(ValueError, match="rebuild required before deploy"):
        asyncio.run(services.deploy_bundle_endpoint("endpoint-1"))

    endpoint = state["endpoints"]["endpoint-1"]
    assert endpoint["deployed_revision_id"] is None
    assert endpoint["deployed_digest"] is None


def test_deploy_bundle_endpoint_requires_matching_prepared_digest(tmp_path, monkeypatch):
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    (bundle_root / "bundle.py").write_text("def run(x):\n    return x\n", encoding="utf-8")
    (bundle_root / "bundle.toml").write_text('name = "demo-bundle"\nversion = "0.1.0"\n', encoding="utf-8")

    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    current_digest = inspect_bundle_preparation(str(bundle_root)).digest
    state = {
        "modules": {
            "mod-1": {"id": "mod-1", "current_revision_id": "rev-1", "current_commit_sha": "abc123", "bundle_version": "0.1.0"},
        },
        "endpoints": {
            "endpoint-1": {
                "id": "endpoint-1",
                "module_import_id": "mod-1",
                "name": "Customer API",
                "prepared_revision_id": "rev-1",
                "prepared_digest": "stale-digest",
                "prepared_image_ref": "registry.test/demo:stale",
                "prepared_image_digest": "sha256:stale",
                "deployed_revision_id": None,
                "deployed_digest": None,
                "deployed_image_ref": None,
                "deployed_image_digest": None,
                "rollout_state": {},
                "rollout_operations": [],
                "rollout_events": [],
            }
        },
    }
    services.postgres_pool = _RolloutPool(state)

    async def fake_get_bundle_endpoint(endpoint_id):
        return _serialize_endpoint_rollout_payload(state, endpoint_id)

    async def fake_resolve_module_execution_state(module_id):
        assert module_id == "mod-1"
        return {
            "module_id": module_id,
            "bundle_path": str(bundle_root),
            "bundle_revision_id": "rev-1",
            "bundle_commit_sha": "abc123",
            "bundle_version": "0.1.0",
            "bundle_name": "demo-bundle",
        }

    monkeypatch.setattr(services, "get_bundle_endpoint", fake_get_bundle_endpoint)
    monkeypatch.setattr(services, "resolve_module_execution_state", fake_resolve_module_execution_state)

    with pytest.raises(ValueError, match="rebuild required before deploy"):
        asyncio.run(services.deploy_bundle_endpoint("endpoint-1"))

    endpoint = state["endpoints"]["endpoint-1"]
    assert endpoint["deployed_revision_id"] is None
    assert endpoint["deployed_digest"] is None
    assert endpoint["prepared_digest"] == "stale-digest"
    assert current_digest != endpoint["prepared_digest"]


def test_restart_bundle_endpoint_runtime_requires_deploy():
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))

    async def fake_get_bundle_endpoint(endpoint_id):
        return {"id": endpoint_id, "deployed_revision_id": None, "restart_generation": 0}

    services.get_bundle_endpoint = fake_get_bundle_endpoint  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="deploy required before restart-runtime"):
        asyncio.run(services.restart_bundle_endpoint_runtime("endpoint-1"))


def test_resolve_bundle_endpoint_execution_state_requires_deployed_revision(monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))

    async def fake_get_bundle_endpoint(endpoint_id):
        return {"id": endpoint_id, "module_import_id": "mod-1", "deployed_revision_id": None}

    async def fake_resolve_bundle_revision_execution_state(revision_id):
        raise AssertionError("bundle revision lookup should not run without a deployed revision")

    async def fake_resolve_module_execution_state(module_id):
        raise AssertionError("module execution fallback should not run without a deployed revision")

    monkeypatch.setattr(services, "get_bundle_endpoint", fake_get_bundle_endpoint)
    monkeypatch.setattr(services, "resolve_bundle_revision_execution_state", fake_resolve_bundle_revision_execution_state)
    monkeypatch.setattr(services, "resolve_module_execution_state", fake_resolve_module_execution_state)

    assert asyncio.run(services.resolve_bundle_endpoint_execution_state("endpoint-1")) is None


def test_resolve_bundle_endpoint_execution_state_includes_deployed_image_metadata(monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))

    async def fake_get_bundle_endpoint(endpoint_id):
        return {
            "id": endpoint_id,
            "module_import_id": "mod-1",
            "deployed_revision_id": "rev-1",
            "deployed_image_ref": "registry.test/prepared/endpoint-1:rev-1",
            "deployed_image_digest": "sha256:deployed",
            "restart_generation": 4,
        }

    async def fake_resolve_bundle_revision_execution_state(revision_id):
        assert revision_id == "rev-1"
        return {"module_id": "mod-1", "bundle_path": "/tmp/bundle", "bundle_revision_id": revision_id}

    monkeypatch.setattr(services, "get_bundle_endpoint", fake_get_bundle_endpoint)
    monkeypatch.setattr(services, "resolve_bundle_revision_execution_state", fake_resolve_bundle_revision_execution_state)

    payload = asyncio.run(services.resolve_bundle_endpoint_execution_state("endpoint-1"))

    assert payload == {
        "module_id": "mod-1",
        "bundle_path": "/tmp/bundle",
        "bundle_revision_id": "rev-1",
        "bundle_image_ref": "registry.test/prepared/endpoint-1:rev-1",
        "bundle_image_digest": "sha256:deployed",
        "restart_generation": 4,
    }


def test_build_module_payload_includes_github_and_revision_metadata():
    services = AppServices(
        Settings(
            postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer",
            module_env_encryption_key=Fernet.generate_key().decode("utf-8"),
        )
    )
    encrypted_entries = services._encrypt_module_environment_entries(
        [
            {"key": "AGENTIC_CHAT_ENDPOINT", "value": "https://example.test", "is_secret": True},
        ]
    )

    payload = services._build_module_payload(
        {
            "id": "mod-1",
            "source": "github",
            "source_ref": "/tmp/dspy-trainer/bundles/mod-1",
            "version_hash": "abc123",
            "bundle_name": "demo-bundle",
            "bundle_version": "1.2.3",
            "status": "validated",
            "created_at": None,
            "validation_status": "passed",
            "smoke_status": "pending",
            "diagnostics": [],
            "github_repo_url": "https://github.com/example/demo-bundle",
            "github_branch": "main",
            "github_secrets_environment_name": "agentic-chat-prod",
            "environment_entries_encrypted": encrypted_entries,
            "checkout_path": "/tmp/dspy-trainer/checkouts/mod-1",
            "current_commit_sha": "abc123",
            "upstream_commit_sha": "abc123",
            "sync_status": "synced",
            "last_synced_at": None,
            "last_sync_error": None,
            "current_revision_id": "rev-1",
            "current_revision_commit_sha": "abc123",
            "current_revision_checkout_path": "/tmp/dspy-trainer/checkouts/mod-1",
            "current_revision_bundle_name": "demo-bundle",
            "current_revision_bundle_version": "1.2.3",
            "current_revision_source_event": "sync",
            "current_revision_created_at": None,
        }
    )

    assert payload["github_repo_url"] == "https://github.com/example/demo-bundle"
    assert payload["github_branch"] == "main"
    assert payload["github_secrets_environment_name"] == "agentic-chat-prod"
    assert payload["environment_entries"][0] == {
        "key": "AGENTIC_CHAT_ENDPOINT",
        "value": "https://example.test",
        "is_secret": True,
    }
    assert payload["checkout_path"] == "/tmp/dspy-trainer/checkouts/mod-1"
    assert payload["current_commit_sha"] == "abc123"
    assert payload["sync_status"] == "synced"
    assert payload["current_revision"] == {
        "id": "rev-1",
        "commit_sha": "abc123",
        "checkout_path": "/tmp/dspy-trainer/checkouts/mod-1",
        "bundle_name": "demo-bundle",
        "bundle_version": "1.2.3",
        "source_event": "sync",
        "created_at": None,
    }


def test_import_github_module_clones_valid_bundle_and_persists_checkout(tmp_path):
    previous_github_pat = os.environ.get("GITHUB_PAT")
    os.environ["GITHUB_PAT"] = "ghp_secret_value"
    services = AppServices(
        Settings(
            postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer",
            checkout_root=str(tmp_path / "checkouts"),
        )
    )
    captured: dict[str, Any] = {}

    async def fake_run_git_command(args, *, cwd=None):
        if args[:2] == ["git", "clone"]:
            clone_target = Path(args[-1])
            clone_target.mkdir(parents=True, exist_ok=True)
            (clone_target / "module.py").write_text(
                "import dspy\nclass Sig(dspy.Signature):\n  q=dspy.InputField()\n  a=dspy.OutputField()\n"
                "class Agent(dspy.Module):\n  def forward(self, q: str):\n    return dspy.Prediction(a='x')\n"
                "def build_program():\n  return Agent()\n",
                encoding="utf-8",
            )
            (clone_target / "metric.py").write_text(
                "def judge_metric(example, prediction, trace=None):\n  return True\n",
                encoding="utf-8",
            )
            (clone_target / "bundle.toml").write_text(
                "name='git-bundle'\nversion='1.2.3'\nscore_pass_threshold=0.8\n",
                encoding="utf-8",
            )
            captured["clone_args"] = args
            return ""
        assert args[:3] == ["git", "rev-parse", "HEAD"]
        assert cwd is not None
        captured["rev_parse_cwd"] = str(cwd)
        return "abc123"

    async def fake_create_module_import(source, source_ref, version_hash, **kwargs):
        captured["create_module_import"] = {
            "source": source,
            "source_ref": source_ref,
            "version_hash": version_hash,
            **kwargs,
        }
        return {"id": kwargs["module_id"], "status": "imported", "current_revision_id": "rev-1"}

    async def fake_set_validation_status(module_id, status, diagnostics):
        captured["validation"] = {
            "module_id": module_id,
            "status": status,
            "diagnostics": diagnostics,
        }
        return True

    services._run_git_command = fake_run_git_command  # type: ignore[method-assign]
    services.create_module_import = fake_create_module_import  # type: ignore[method-assign]
    services.set_validation_status = fake_set_validation_status  # type: ignore[method-assign]

    try:
        result = asyncio.run(
            services.import_github_module(
                "https://github.com/example/demo-bundle.git",
                "main",
            )
        )
    finally:
        if previous_github_pat is None:
            os.environ.pop("GITHUB_PAT", None)
        else:
            os.environ["GITHUB_PAT"] = previous_github_pat

    assert result["status"] == "imported"
    assert result["validation_status"] == "passed"
    assert result["github_repo_url"] == "https://github.com/example/demo-bundle"
    assert result["github_branch"] == "main"
    assert result["current_commit_sha"] == "abc123"
    assert captured["create_module_import"]["source"] == "github"
    assert captured["create_module_import"]["github_repo_url"] == "https://github.com/example/demo-bundle"
    assert captured["create_module_import"]["bundle_name"] == "git-bundle"
    assert captured["create_module_import"]["bundle_version"] == "1.2.3"
    assert captured["validation"]["status"] == "passed"
    assert "x-access-token:" in captured["clone_args"][6]
    assert result.get("github_pat") is None


def test_import_github_module_rejects_invalid_repo_root_and_cleans_checkout(tmp_path):
    previous_github_pat = os.environ.get("GITHUB_PAT")
    os.environ["GITHUB_PAT"] = "ghp_secret_value"
    services = AppServices(
        Settings(
            postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer",
            checkout_root=str(tmp_path / "checkouts"),
        )
    )

    async def fake_run_git_command(args, *, cwd=None):
        del cwd
        if args[:2] == ["git", "clone"]:
            clone_target = Path(args[-1])
            clone_target.mkdir(parents=True, exist_ok=True)
            (clone_target / "README.md").write_text("not a bundle", encoding="utf-8")
            return ""
        return "abc123"

    services._run_git_command = fake_run_git_command  # type: ignore[method-assign]

    try:
        try:
            asyncio.run(
                services.import_github_module(
                    "https://github.com/example/not-a-bundle",
                    "main",
                )
            )
        except ValueError as exc:
            assert str(exc) == "Validation failed with 3 errors."
        else:
            raise AssertionError("expected import_github_module to reject invalid bundle root")
    finally:
        if previous_github_pat is None:
            os.environ.pop("GITHUB_PAT", None)
        else:
            os.environ["GITHUB_PAT"] = previous_github_pat

    checkout_root = tmp_path / "checkouts"
    assert list(checkout_root.glob("*")) == []


def test_classify_sync_status_covers_sync_relationships():
    assert _classify_sync_status("abc", "abc", "abc") == "synced"
    assert _classify_sync_status("abc", "def", "abc") == "behind"
    assert _classify_sync_status("def", "abc", "abc") == "ahead"
    assert _classify_sync_status("abc", "def", "xyz") == "diverged"


def test_ensure_bundle_requirements_installed_skips_when_missing(tmp_path, monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer", checkout_root=str(tmp_path / "checkouts")))
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()

    called = False

    async def fake_exec(*args, **kwargs):
        nonlocal called
        called = True
        return _FakeAsyncProcess()

    async def fake_shell(*args, **kwargs):
        nonlocal called
        called = True
        return _FakeAsyncProcess()

    monkeypatch.setattr("app.services.asyncio.create_subprocess_exec", fake_exec)
    monkeypatch.setattr("app.services.asyncio.create_subprocess_shell", fake_shell)

    asyncio.run(services.ensure_bundle_requirements_installed(str(bundle_root)))

    assert called is False


def test_ensure_bundle_requirements_installed_caches_by_requirements_hash(tmp_path, monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer", checkout_root=str(tmp_path / "checkouts")))
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    requirements = bundle_root / "requirements.txt"
    requirements.write_text("httpx==0.27.0\n", encoding="utf-8")

    calls: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        del kwargs
        calls.append(list(args))
        return _FakeAsyncProcess()

    monkeypatch.setattr("app.services.asyncio.create_subprocess_exec", fake_exec)

    asyncio.run(services.ensure_bundle_requirements_installed(str(bundle_root)))
    asyncio.run(services.ensure_bundle_requirements_installed(str(bundle_root)))
    requirements.write_text("httpx==0.28.0\n", encoding="utf-8")
    asyncio.run(services.ensure_bundle_requirements_installed(str(bundle_root)))

    assert len(calls) == 2
    assert calls[0][0:5] == [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    assert "--target" in calls[0]
    assert calls[0][-2:] == ["-r", str(requirements)]


def test_inspect_bundle_preparation_digest_changes_for_local_requirement_inputs(tmp_path):
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    package_dir = bundle_root / "pkg"
    package_dir.mkdir()
    (package_dir / "pyproject.toml").write_text("[project]\nname='pkg'\nversion='0.1.0'\n", encoding="utf-8")
    (package_dir / "module.py").write_text("VALUE = 'before'\n", encoding="utf-8")
    wheel_path = bundle_root / "dist.whl"
    wheel_path.write_bytes(b"wheel-v1")
    nested = bundle_root / "nested.txt"
    nested.write_text("./dist.whl\n", encoding="utf-8")
    (bundle_root / "requirements.txt").write_text("-e ./pkg\n-r nested.txt\n", encoding="utf-8")

    initial = inspect_bundle_preparation(str(bundle_root)).digest
    (package_dir / "module.py").write_text("VALUE = 'after'\n", encoding="utf-8")
    assert inspect_bundle_preparation(str(bundle_root)).digest != initial

    second = inspect_bundle_preparation(str(bundle_root)).digest
    wheel_path.write_bytes(b"wheel-v2")
    assert inspect_bundle_preparation(str(bundle_root)).digest != second


def test_ensure_bundle_requirements_installed_rebuilds_when_local_requirement_input_changes(tmp_path, monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer", checkout_root=str(tmp_path / "checkouts")))
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    package_dir = bundle_root / "pkg"
    package_dir.mkdir()
    (package_dir / "pyproject.toml").write_text("[project]\nname='pkg'\nversion='0.1.0'\n", encoding="utf-8")
    local_module = package_dir / "module.py"
    local_module.write_text("VALUE = 'before'\n", encoding="utf-8")
    requirements = bundle_root / "requirements.txt"
    requirements.write_text("-e ./pkg\n", encoding="utf-8")

    calls: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        del kwargs
        calls.append(list(args))
        return _FakeAsyncProcess()

    monkeypatch.setattr("app.services.asyncio.create_subprocess_exec", fake_exec)

    asyncio.run(services.ensure_bundle_requirements_installed(str(bundle_root)))
    asyncio.run(services.ensure_bundle_requirements_installed(str(bundle_root)))
    local_module.write_text("VALUE = 'after'\n", encoding="utf-8")
    asyncio.run(services.ensure_bundle_requirements_installed(str(bundle_root)))

    assert len(calls) == 2
    assert calls[0][-2:] == ["-r", str(requirements)]
    assert calls[1][-2:] == ["-r", str(requirements)]


def test_ensure_bundle_requirements_installed_runs_system_commands_before_pip(tmp_path, monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer", checkout_root=str(tmp_path / "checkouts")))
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    (bundle_root / "bundle.toml").write_text(
        "name='x'\nversion='0.1.0'\nscore_pass_threshold=0.8\n[runtime]\nsystem_dependency_commands=['echo system-1','echo system-2']\n",
        encoding="utf-8",
    )
    requirements = bundle_root / "requirements.txt"
    requirements.write_text("httpx==0.27.0\n", encoding="utf-8")

    calls: list[tuple[str, list[str] | str, str | None]] = []

    async def fake_shell(command, **kwargs):
        calls.append(("shell", command, kwargs.get("cwd")))
        return _FakeAsyncProcess()

    async def fake_exec(*args, **kwargs):
        calls.append(("exec", list(args), kwargs.get("cwd")))
        return _FakeAsyncProcess()

    monkeypatch.setattr("app.services.asyncio.create_subprocess_shell", fake_shell)
    monkeypatch.setattr("app.services.asyncio.create_subprocess_exec", fake_exec)

    asyncio.run(services.ensure_bundle_requirements_installed(str(bundle_root)))

    assert calls[0] == ("shell", "echo system-1", str(bundle_root))
    assert calls[1] == ("shell", "echo system-2", str(bundle_root))
    assert calls[2][0] == "exec"
    assert calls[2][1][0:5] == [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    assert "--target" in calls[2][1]
    assert calls[2][1][-2:] == ["-r", str(requirements)]
    assert calls[2][2] == str(bundle_root)


def test_ensure_bundle_requirements_installed_reuses_shared_preparation_artifact_across_service_instances(tmp_path, monkeypatch):
    settings = Settings(
        postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer",
        checkout_root=str(tmp_path / "checkouts"),
    )
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    requirements = bundle_root / "requirements.txt"
    requirements.write_text("httpx==0.27.0\n", encoding="utf-8")

    calls: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        del kwargs
        calls.append(list(args))
        return _FakeAsyncProcess()

    monkeypatch.setattr("app.services.asyncio.create_subprocess_exec", fake_exec)

    asyncio.run(AppServices(settings).ensure_bundle_requirements_installed(str(bundle_root)))
    asyncio.run(AppServices(settings).ensure_bundle_requirements_installed(str(bundle_root)))

    assert len(calls) == 1


def test_activate_bundle_preparation_loads_prepared_shared_artifact(tmp_path, monkeypatch):
    checkout_root = tmp_path / "checkouts"
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    (bundle_root / "requirements.txt").write_text("httpx==0.27.0\n", encoding="utf-8")
    spec = inspect_bundle_preparation(str(bundle_root))
    cache_root = bundle_preparation_cache_root(str(checkout_root))
    site_packages_dir = cache_root / spec.digest / "site-packages"
    site_packages_dir.mkdir(parents=True)
    (site_packages_dir / "prepared_bundle_marker.py").write_text("VALUE = 'shared-artifact'\n", encoding="utf-8")
    (cache_root / spec.digest / "prepared.json").write_text("{}", encoding="utf-8")

    added: list[str] = []
    monkeypatch.setattr("app.bundle_preparation.site.addsitedir", lambda path: added.append(path))

    activated = activate_bundle_preparation(str(bundle_root), str(checkout_root))

    assert activated == site_packages_dir
    assert added == [str(site_packages_dir)]


def test_ensure_bundle_requirements_installed_allows_single_shared_builder_for_concurrent_calls(tmp_path, monkeypatch):
    settings = Settings(
        postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer",
        checkout_root=str(tmp_path / "checkouts"),
    )
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    requirements = bundle_root / "requirements.txt"
    requirements.write_text("httpx==0.27.0\n", encoding="utf-8")

    calls: list[list[str]] = []
    started = asyncio.Event()
    release = asyncio.Event()

    class _BlockingFakeAsyncProcess(_FakeAsyncProcess):
        async def wait(self):
            started.set()
            await release.wait()
            return await super().wait()

    async def fake_exec(*args, **kwargs):
        del kwargs
        calls.append(list(args))
        return _BlockingFakeAsyncProcess()

    monkeypatch.setattr("app.services.asyncio.create_subprocess_exec", fake_exec)

    async def run_test():
        services_a = AppServices(settings)
        services_b = AppServices(settings)
        task_a = asyncio.create_task(services_a.ensure_bundle_requirements_installed(str(bundle_root)))
        await started.wait()
        task_b = asyncio.create_task(services_b.ensure_bundle_requirements_installed(str(bundle_root)))
        await asyncio.sleep(0.3)
        release.set()
        await asyncio.gather(task_a, task_b)

    asyncio.run(run_test())

    assert len(calls) == 1


def test_ensure_bundle_requirements_installed_surfaces_system_command_failure(tmp_path, monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer", checkout_root=str(tmp_path / "checkouts")))
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    (bundle_root / "bundle.toml").write_text(
        "name='x'\nversion='0.1.0'\nscore_pass_threshold=0.8\n[runtime]\nsystem_dependency_commands=['exit 7']\n",
        encoding="utf-8",
    )

    async def fake_shell(*args, **kwargs):
        del args, kwargs
        return _FakeAsyncProcess(returncode=7, stderr="failed")

    monkeypatch.setattr("app.services.asyncio.create_subprocess_shell", fake_shell)

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(services.ensure_bundle_requirements_installed(str(bundle_root)))

    assert str(exc_info.value) == "failed"


def test_build_prepared_bundle_image_builds_revision_and_digest_tagged_image(tmp_path, monkeypatch):
    services = AppServices(
        Settings(
            postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer",
            checkout_root=str(tmp_path / "checkouts"),
            prepared_endpoint_image_repository="registry.test/prepared-endpoints",
        )
    )
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    (bundle_root / "bundle.toml").write_text(
        "name='x'\nversion='0.1.0'\nscore_pass_threshold=0.8\n[runtime]\nsystem_dependency_commands=['echo image-build']\n",
        encoding="utf-8",
    )
    (bundle_root / "requirements.txt").write_text("httpx==0.27.0\n", encoding="utf-8")
    spec = inspect_bundle_preparation(str(bundle_root))
    prepared_dir = bundle_preparation_cache_root(str(tmp_path / "checkouts")) / spec.digest
    (prepared_dir / "site-packages").mkdir(parents=True)
    (prepared_dir / "prepared.json").write_text("{}", encoding="utf-8")

    calls: list[tuple[str, ...]] = []
    dockerfile_text = ""

    async def fake_run_subprocess(*argv, cwd=None):
        nonlocal dockerfile_text
        del cwd
        calls.append(tuple(argv))
        if argv[0:2] == ("docker", "build"):
            dockerfile_text = (Path(argv[4]) / "Dockerfile").read_text(encoding="utf-8")
            return ("", "")
        if argv[0:3] == ("docker", "image", "inspect"):
            return ("sha256:built", "")
        return ("", "")

    monkeypatch.setattr(services, "_run_subprocess", fake_run_subprocess)

    payload = asyncio.run(
        services._build_prepared_bundle_image(
            endpoint_id="endpoint-1",
            bundle_path=str(bundle_root),
            revision_id="rev-1",
            digest=spec.digest,
            existing_image_ref=None,
            existing_image_digest=None,
        )
    )

    assert payload == {
        "image_ref": f"registry.test/prepared-endpoints/endpoint-1:rev-rev-1-prep-{spec.digest[:24]}",
        "image_digest": "sha256:built",
        "reused": False,
    }
    assert calls[0][0:3] == ("docker", "build", "--tag")
    assert calls[0][3] == payload["image_ref"]
    assert "FROM python:3.11-slim" in dockerfile_text
    assert 'RUN ["/bin/sh", "-lc", "echo image-build"]' in dockerfile_text
    assert calls[1] == ("docker", "image", "inspect", payload["image_ref"], "--format", "{{.Id}}")


def test_build_prepared_bundle_image_reuses_existing_matching_local_image(tmp_path, monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    calls: list[tuple[str, ...]] = []

    async def fake_run_subprocess(*argv, cwd=None):
        del cwd
        calls.append(tuple(argv))
        return ("sha256:prepared", "")

    monkeypatch.setattr(services, "_run_subprocess", fake_run_subprocess)

    payload = asyncio.run(
        services._build_prepared_bundle_image(
            endpoint_id="endpoint-1",
            bundle_path=str(tmp_path / "bundle"),
            revision_id="rev-1",
            digest="digest-1",
            existing_image_ref="registry.test/prepared/endpoint-1:rev-1",
            existing_image_digest="sha256:prepared",
        )
    )

    assert payload == {
        "image_ref": "registry.test/prepared/endpoint-1:rev-1",
        "image_digest": "sha256:prepared",
        "reused": True,
    }
    assert calls == [
        ("docker", "image", "inspect", "registry.test/prepared/endpoint-1:rev-1", "--format", "{{.Id}}")
    ]


def test_rebuild_bundle_endpoint_reuses_matching_prepared_image_metadata(tmp_path, monkeypatch):
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer", checkout_root=str(tmp_path / "checkouts")))
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    (bundle_root / "requirements.txt").write_text("httpx==0.27.0\n", encoding="utf-8")
    digest = inspect_bundle_preparation(str(bundle_root)).digest
    state = {
        "modules": {"mod-1": {"id": "mod-1", "current_revision_id": "rev-1", "current_commit_sha": "abc123", "bundle_version": "0.1.0"}},
        "endpoints": {
            "endpoint-1": {
                "id": "endpoint-1",
                "module_import_id": "mod-1",
                "lm_profile_id": None,
                "pinned_worker_count": 1,
                "name": "Customer API",
                "key_preview": "abc123",
                "prepared_revision_id": "rev-1",
                "prepared_digest": digest,
                "prepared_image_ref": "registry.test/prepared-endpoints/endpoint-1:rev-rev-1-prep-cache",
                "prepared_image_digest": "sha256:prepared",
                "prepared_at": None,
                "deployed_revision_id": None,
                "deployed_digest": None,
                "deployed_image_ref": None,
                "deployed_image_digest": None,
                "deployed_at": None,
                "restart_generation": 0,
                "rollout_state": {},
                "rollout_operations": [],
                "rollout_events": [],
                "created_at": None,
                "updated_at": None,
            }
        },
    }
    services.postgres_pool = _RolloutPool(state)

    async def fake_get_bundle_endpoint(endpoint_id):
        return _serialize_endpoint_rollout_payload(state, endpoint_id)

    async def fake_resolve_module_execution_state(module_id):
        return {
            "module_id": module_id,
            "bundle_path": str(bundle_root),
            "bundle_revision_id": "rev-1",
            "bundle_commit_sha": "abc123",
            "bundle_version": "0.1.0",
            "bundle_name": "demo-bundle",
        }

    async def fake_ensure_bundle_requirements_installed(bundle_path):
        assert bundle_path == str(bundle_root)
        return None

    observed_kwargs: list[dict[str, Any]] = []

    async def fake_build_prepared_bundle_image(**kwargs):
        observed_kwargs.append(kwargs)
        return {
            "image_ref": kwargs["existing_image_ref"],
            "image_digest": kwargs["existing_image_digest"],
            "reused": True,
        }

    monkeypatch.setattr(services, "get_bundle_endpoint", fake_get_bundle_endpoint)
    monkeypatch.setattr(services, "resolve_module_execution_state", fake_resolve_module_execution_state)
    monkeypatch.setattr(services, "ensure_bundle_requirements_installed", fake_ensure_bundle_requirements_installed)
    monkeypatch.setattr(services, "_build_prepared_bundle_image", fake_build_prepared_bundle_image)

    payload = asyncio.run(services.rebuild_bundle_endpoint("endpoint-1"))

    assert payload is not None
    assert payload["prepared_image_ref"] == "registry.test/prepared-endpoints/endpoint-1:rev-rev-1-prep-cache"
    assert payload["prepared_image_digest"] == "sha256:prepared"
    assert payload["rollout_operations"][0]["metadata"]["prepared_image_reused"] is True
    assert observed_kwargs == [
        {
            "endpoint_id": "endpoint-1",
            "bundle_path": str(bundle_root),
            "revision_id": "rev-1",
            "digest": digest,
            "existing_image_ref": "registry.test/prepared-endpoints/endpoint-1:rev-rev-1-prep-cache",
            "existing_image_digest": "sha256:prepared",
        }
    ]


def test_json_ready_serializes_decimal_values():
    payload = {
        "table_rows": [
            {"revenue": Decimal("3577181.22")},
        ],
        "raw_output": {"value": Decimal("31.13")},
    }

    normalized = _json_ready(payload)

    assert normalized == {
        "table_rows": [{"revenue": "3577181.22"}],
        "raw_output": {"value": "31.13"},
    }


def test_publish_endpoint_invocation_event_serializes_decimal_payloads():
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    publisher = _RedisPublisher()
    setattr(services, "redis", publisher)

    asyncio.run(
        services.publish_endpoint_invocation_event(
            "inv-1",
            "final",
            {"table_rows": [{"sales": Decimal("655129.55")}]},
        )
    )

    assert len(publisher.messages) == 1
    channel, payload = publisher.messages[0]
    assert channel.endswith("inv-1")
    assert '"655129.55"' in payload


def test_module_env_encryption_key_error_mentions_lm_profile_api_keys():
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))

    with pytest.raises(RuntimeError) as exc_info:
        services._get_module_env_fernet()

    assert str(exc_info.value) == (
        "DSPY_TRAINER_MODULE_ENV_ENCRYPTION_KEY is required to store module environment entries and LM profile API keys"
    )


def test_lm_profile_api_key_decrypt_error_mentions_shared_encryption_scope():
    services = AppServices(
        Settings(
            postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer",
            module_env_encryption_key=Fernet.generate_key().decode("utf-8"),
        )
    )

    with pytest.raises(RuntimeError) as exc_info:
        services._decrypt_lm_profile_api_key("not-a-valid-fernet-token")

    assert str(exc_info.value) == (
        "module environment entries or LM profile API keys could not be decrypted with the configured key"
    )
