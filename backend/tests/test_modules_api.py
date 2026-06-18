import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_mod
from app.services import ModuleSyncError, ReadinessStatus


STORE: dict[str, dict] = {}
ENDPOINTS: dict[str, dict] = {}


class FakePubSub:
    def __init__(self, redis):
        self.redis = redis
        self.channel = None

    async def subscribe(self, channel):
        self.channel = channel

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        del ignore_subscribe_messages, timeout
        if not self.channel:
            return None
        queued = self.redis.channels.get(self.channel, [])
        if not queued:
            return None
        return {"data": json.dumps(queued.pop(0))}

    async def unsubscribe(self, channel):
        del channel

    async def close(self):
        return None


class FakeRedis:
    def __init__(self):
        self.channels: dict[str, list[dict]] = {}

    def pubsub(self):
        return FakePubSub(self)

    async def set(self, key, value, ex=None):
        del key, value, ex

    async def get(self, key):
        del key
        return None

    async def delete(self, key):
        del key

    async def keys(self, pattern):
        del pattern
        return []

    async def execute_command(self, *args):
        del args
        return None

    async def publish(self, channel, payload):
        self.channels.setdefault(channel, []).append(json.loads(payload))


async def fake_connect(self):
    self.redis = FakeRedis()
    return None


async def fake_disconnect(self):
    return None


async def fake_readiness(self):
    return ReadinessStatus(postgres=True, redis=True, mlflow=True, litellm=True)


async def fake_list_endpoint_workers(self):
    return {
        "items": [
            {"worker_id": "endpoint-worker-1", "status": "listening", "endpoint_id": "endpoint-1", "task_id": None, "last_seen": None, "kind": "endpoint"},
            {"worker_id": "endpoint-worker-2", "status": "running", "endpoint_id": "endpoint-1", "task_id": "inv-1", "last_seen": None, "kind": "endpoint"},
        ],
        "total_workers": 2,
        "reported_workers": 2,
        "available_workers": 1,
        "busy_workers": 1,
    }


async def fake_create_module_import(self, source, source_ref, version_hash, **kwargs):
    module_id = "mod-1"
    STORE[module_id] = {
        "id": module_id,
        "status": "imported",
        "validation_status": "pending",
        "smoke_status": "pending",
        "diagnostics": [],
        "source": source,
        "source_ref": source_ref,
        "version_hash": version_hash,
        "github_repo_url": kwargs.get("github_repo_url"),
        "github_branch": kwargs.get("github_branch"),
        "github_subpath": kwargs.get("github_subpath"),
        "github_secrets_environment_name": kwargs.get("github_secrets_environment_name"),
        "environment_entries": kwargs.get("environment_entries") or [],
        "checkout_path": kwargs.get("checkout_path") or source_ref,
        "current_commit_sha": kwargs.get("current_commit_sha") or version_hash,
        "upstream_commit_sha": kwargs.get("upstream_commit_sha") or kwargs.get("current_commit_sha") or version_hash,
        "sync_status": kwargs.get("sync_status") or ("synced" if source == "github" else "legacy"),
        "current_revision_id": "rev-1",
        "current_revision": {
            "id": "rev-1",
            "commit_sha": kwargs.get("current_commit_sha") or version_hash,
            "checkout_path": kwargs.get("checkout_path") or source_ref,
            "bundle_name": None,
            "bundle_version": None,
            "source_event": "import",
            "created_at": None,
        },
    }
    return {"id": module_id, "status": "imported"}


async def fake_set_validation_status(self, module_id, status, diagnostics, **kwargs):
    if module_id not in STORE:
        return False
    STORE[module_id]["validation_status"] = status
    STORE[module_id]["status"] = "validated" if status == "passed" else "validation_failed"
    STORE[module_id]["diagnostics"] = diagnostics
    STORE[module_id]["validation_revision_id"] = kwargs.get("revision_id")
    STORE[module_id]["validation_commit_sha"] = kwargs.get("commit_sha")
    STORE[module_id]["validation_bundle_version"] = kwargs.get("bundle_version")
    return True


async def fake_set_smoke_status(self, module_id, status, diagnostics, **kwargs):
    if module_id not in STORE:
        return False
    STORE[module_id]["smoke_status"] = status
    if status == "running":
        STORE[module_id]["status"] = "smoke_testing"
    else:
        STORE[module_id]["status"] = "runnable" if status == "passed" else "smoke_failed"
    STORE[module_id]["diagnostics"] = diagnostics
    STORE[module_id]["smoke_revision_id"] = kwargs.get("revision_id")
    STORE[module_id]["smoke_commit_sha"] = kwargs.get("commit_sha")
    STORE[module_id]["smoke_bundle_version"] = kwargs.get("bundle_version")
    return True


async def fake_get_diagnostics(self, module_id):
    return STORE.get(module_id)


async def fake_set_module_bundle_metadata(self, module_id, bundle_name, bundle_version):
    if module_id not in STORE:
        return
    STORE[module_id]["bundle_name"] = bundle_name
    STORE[module_id]["bundle_version"] = bundle_version


async def fake_set_module_source_ref(self, module_id, source_ref):
    if module_id not in STORE:
        return
    STORE[module_id]["source_ref"] = source_ref


async def fake_set_module_environment_config(self, module_id, github_secrets_environment_name, environment_entries):
    if module_id not in STORE:
        return
    STORE[module_id]["github_secrets_environment_name"] = github_secrets_environment_name
    STORE[module_id]["environment_entries"] = environment_entries


async def fake_get_module_runtime_environment(self, module_id):
    module = STORE.get(module_id)
    if module is None:
        return {}
    return {entry["key"]: entry["value"] for entry in module.get("environment_entries", [])}


async def fake_get_module(self, module_id):
    return STORE.get(module_id)


async def fake_list_modules(self):
    return list(STORE.values())


async def fake_list_bundle_endpoints(self, module_id):
    if module_id not in STORE:
        return None
    return [endpoint for endpoint in ENDPOINTS.values() if endpoint["module_import_id"] == module_id]


async def fake_list_all_bundle_endpoints(self):
    return list(ENDPOINTS.values())


async def fake_get_bundle_endpoint(self, endpoint_id):
    return ENDPOINTS.get(endpoint_id)


async def fake_get_lm_profile(self, lm_profile_id):
    if lm_profile_id == "lm-1":
        return {
            "id": "lm-1",
            "name": "Primary LM",
            "model": "openai/gpt-4o-mini",
            "api_base": "http://litellm:4000",
            "model_type": "responses",
            "default_params": {},
            "virtual_key": "vk-lm-1",
        }
    if lm_profile_id == "lm-2":
        return {
            "id": "lm-2",
            "name": "Backup LM",
            "model": "openai/gpt-4.1-mini",
            "api_base": "http://litellm:4000",
            "model_type": "responses",
            "default_params": {},
            "virtual_key": "vk-lm-2",
        }
    return None


async def fake_create_bundle_endpoint(self, module_id, name, lm_profile_id=None, pinned_worker_count=1):
    if module_id not in STORE:
        return None
    endpoint_id = f"endpoint-{len(ENDPOINTS) + 1}"
    endpoint = {
        "id": endpoint_id,
        "module_import_id": module_id,
        "lm_profile_id": lm_profile_id,
        "pinned_worker_count": pinned_worker_count,
        "name": name,
        "key_preview": "abc123",
        "api_key": f"bep-{endpoint_id}",
        "created_at": None,
        "updated_at": None,
    }
    ENDPOINTS[endpoint_id] = endpoint
    return endpoint


async def fake_create_bundle_endpoint_global(self, name, module_import_id, lm_profile_id=None, pinned_worker_count=1):
    return await fake_create_bundle_endpoint(self, module_import_id, name, lm_profile_id, pinned_worker_count)


async def fake_update_bundle_endpoint(self, module_id, endpoint_id, name, lm_profile_id=None, pinned_worker_count=None):
    endpoint = ENDPOINTS.get(endpoint_id)
    if endpoint is None or endpoint["module_import_id"] != module_id:
        return None
    endpoint["name"] = name
    endpoint["lm_profile_id"] = lm_profile_id
    if pinned_worker_count is not None:
        endpoint["pinned_worker_count"] = pinned_worker_count
    return endpoint


async def fake_update_bundle_endpoint_global(self, endpoint_id, *, name=None, module_import_id=None, lm_profile_id=None, pinned_worker_count=None):
    endpoint = ENDPOINTS.get(endpoint_id)
    if endpoint is None:
        return None
    if module_import_id is not None:
        if module_import_id not in STORE:
            raise ValueError("module not found")
        endpoint["module_import_id"] = module_import_id
    if name is not None:
        endpoint["name"] = name
    if lm_profile_id is not None:
        endpoint["lm_profile_id"] = lm_profile_id
    if pinned_worker_count is not None:
        endpoint["pinned_worker_count"] = pinned_worker_count
    return endpoint


async def fake_delete_bundle_endpoint(self, module_id, endpoint_id):
    endpoint = ENDPOINTS.get(endpoint_id)
    if endpoint is None or endpoint["module_import_id"] != module_id:
        return False
    del ENDPOINTS[endpoint_id]
    return True


async def fake_delete_bundle_endpoint_global(self, endpoint_id):
    endpoint = ENDPOINTS.get(endpoint_id)
    if endpoint is None:
        return False
    del ENDPOINTS[endpoint_id]
    return True


async def fake_regenerate_bundle_endpoint_key(self, module_id, endpoint_id):
    endpoint = ENDPOINTS.get(endpoint_id)
    if endpoint is None or endpoint["module_import_id"] != module_id:
        return None
    endpoint["api_key"] = f"rotated-{endpoint_id}"
    endpoint["key_preview"] = "rot999"
    return endpoint


async def fake_regenerate_bundle_endpoint_key_global(self, endpoint_id):
    endpoint = ENDPOINTS.get(endpoint_id)
    if endpoint is None:
        return None
    endpoint["api_key"] = f"rotated-{endpoint_id}"
    endpoint["key_preview"] = "rot999"
    return endpoint


async def fake_authenticate_bundle_endpoint(self, endpoint_id, api_key):
    endpoint = ENDPOINTS.get(endpoint_id)
    if endpoint is None or endpoint.get("api_key") != api_key:
        return None
    return endpoint


async def fake_enqueue_endpoint_invocation(self, endpoint_id, input_payload, *, stream, invocation_id=None):
    channel = self._endpoint_invocation_channel(invocation_id)
    endpoint = ENDPOINTS.get(endpoint_id)
    if endpoint is None:
        raise RuntimeError("endpoint not found")
    if stream:
        self.redis.channels.setdefault(channel, []).append({"event": "delta", "payload": {"chunk": 1, "input": input_payload}})
    self.redis.channels.setdefault(channel, []).append({"event": "final", "payload": {"echo": input_payload, "bundle": endpoint["name"], "done": stream}})
    return invocation_id


async def fake_reconcile_endpoint_worker_assignments(self):
    return None


async def fake_list_module_revisions(self, module_id):
    if module_id not in STORE:
        return []
    return [
        {
            "id": "rev-1",
            "commit_sha": "abc12345",
            "bundle_version": STORE[module_id].get("bundle_version") or "0.1.0",
            "source_event": "import",
            "created_at": None,
        }
    ]


async def fake_import_github_module(self, github_repo_url, github_branch, github_subpath=None):
    module_id = "mod-1"
    STORE[module_id] = {
        "id": module_id,
        "status": "validated",
        "validation_status": "passed",
        "smoke_status": "pending",
        "diagnostics": [],
        "source": "github",
        "source_ref": "/tmp/dspy-trainer/checkouts/mod-1/bundles/support",
        "version_hash": "abc123",
        "github_repo_url": github_repo_url,
        "github_branch": github_branch,
        "github_subpath": github_subpath,
        "github_secrets_environment_name": None,
        "environment_entries": [],
        "checkout_path": "/tmp/dspy-trainer/checkouts/mod-1",
        "current_commit_sha": "abc123",
        "upstream_commit_sha": "abc123",
        "sync_status": "synced",
        "current_revision_id": "rev-1",
        "current_revision": {
            "id": "rev-1",
            "commit_sha": "abc123",
            "checkout_path": "/tmp/dspy-trainer/checkouts/mod-1/bundles/support",
            "bundle_name": "demo-bundle",
            "bundle_version": "1.2.3",
            "source_event": "import",
            "created_at": None,
        },
    }
    return {"id": module_id, "status": "validated"}


async def fake_refresh_module_sync_status(self, module_id):
    module = STORE.get(module_id)
    if module is None:
        raise ValueError("module not found")
    return {
        "module_id": module_id,
        "sync_status": module.get("sync_status", "legacy"),
        "current_commit_sha": module.get("current_commit_sha"),
        "upstream_commit_sha": module.get("upstream_commit_sha"),
        "github_branch": module.get("github_branch"),
        "github_repo_url": module.get("github_repo_url"),
        "last_sync_error": module.get("last_sync_error"),
    }


async def fake_sync_module(self, module_id):
    state = await fake_refresh_module_sync_status(self, module_id)
    module = STORE[module_id]
    if state["sync_status"] != "behind":
        raise ModuleSyncError("module is not eligible for fast-forward sync", sync_state=state)
    module["current_commit_sha"] = module["upstream_commit_sha"]
    module["sync_status"] = "synced"
    return {
        **state,
        "sync_status": "synced",
        "current_commit_sha": module["current_commit_sha"],
        "synced": True,
    }


async def fake_ensure_module_mutation_allowed(self, module_id):
    state = await fake_refresh_module_sync_status(self, module_id)
    if state["sync_status"] in {"behind", "diverged", "sync_error"}:
        raise ModuleSyncError("module has upstream changes that must be synced before mutation", sync_state=state)
    return state


def _patch_services(monkeypatch):
    monkeypatch.setenv("DSPY_TRAINER_POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/dspy_trainer")
    monkeypatch.setattr(main_mod.AppServices, "connect", fake_connect)
    monkeypatch.setattr(main_mod.AppServices, "disconnect", fake_disconnect)
    monkeypatch.setattr(main_mod.AppServices, "readiness", fake_readiness)
    monkeypatch.setattr(main_mod.AppServices, "list_endpoint_workers", fake_list_endpoint_workers)
    monkeypatch.setattr(main_mod.AppServices, "create_module_import", fake_create_module_import)
    monkeypatch.setattr(main_mod.AppServices, "set_validation_status", fake_set_validation_status)
    monkeypatch.setattr(main_mod.AppServices, "set_smoke_status", fake_set_smoke_status)
    monkeypatch.setattr(main_mod.AppServices, "get_diagnostics", fake_get_diagnostics)
    monkeypatch.setattr(main_mod.AppServices, "get_module", fake_get_module)
    monkeypatch.setattr(main_mod.AppServices, "list_modules", fake_list_modules)
    monkeypatch.setattr(main_mod.AppServices, "list_module_revisions", fake_list_module_revisions)
    monkeypatch.setattr(main_mod.AppServices, "import_github_module", fake_import_github_module)
    monkeypatch.setattr(main_mod.AppServices, "refresh_module_sync_status", fake_refresh_module_sync_status)
    monkeypatch.setattr(main_mod.AppServices, "sync_module", fake_sync_module)
    monkeypatch.setattr(main_mod.AppServices, "ensure_module_mutation_allowed", fake_ensure_module_mutation_allowed)
    monkeypatch.setattr(main_mod.AppServices, "set_module_bundle_metadata", fake_set_module_bundle_metadata)
    monkeypatch.setattr(main_mod.AppServices, "set_module_source_ref", fake_set_module_source_ref)
    monkeypatch.setattr(main_mod.AppServices, "set_module_environment_config", fake_set_module_environment_config)
    monkeypatch.setattr(main_mod.AppServices, "get_module_runtime_environment", fake_get_module_runtime_environment)
    monkeypatch.setattr(main_mod.AppServices, "list_bundle_endpoints", fake_list_bundle_endpoints)
    monkeypatch.setattr(main_mod.AppServices, "list_all_bundle_endpoints", fake_list_all_bundle_endpoints)
    monkeypatch.setattr(main_mod.AppServices, "get_bundle_endpoint", fake_get_bundle_endpoint)
    monkeypatch.setattr(main_mod.AppServices, "get_lm_profile", fake_get_lm_profile)
    monkeypatch.setattr(main_mod.AppServices, "create_bundle_endpoint", fake_create_bundle_endpoint)
    monkeypatch.setattr(main_mod.AppServices, "create_bundle_endpoint_global", fake_create_bundle_endpoint_global)
    monkeypatch.setattr(main_mod.AppServices, "update_bundle_endpoint", fake_update_bundle_endpoint)
    monkeypatch.setattr(main_mod.AppServices, "update_bundle_endpoint_global", fake_update_bundle_endpoint_global)
    monkeypatch.setattr(main_mod.AppServices, "delete_bundle_endpoint", fake_delete_bundle_endpoint)
    monkeypatch.setattr(main_mod.AppServices, "delete_bundle_endpoint_global", fake_delete_bundle_endpoint_global)
    monkeypatch.setattr(main_mod.AppServices, "regenerate_bundle_endpoint_key", fake_regenerate_bundle_endpoint_key)
    monkeypatch.setattr(main_mod.AppServices, "regenerate_bundle_endpoint_key_global", fake_regenerate_bundle_endpoint_key_global)
    monkeypatch.setattr(main_mod.AppServices, "authenticate_bundle_endpoint", fake_authenticate_bundle_endpoint)
    monkeypatch.setattr(main_mod.AppServices, "enqueue_endpoint_invocation", fake_enqueue_endpoint_invocation)
    monkeypatch.setattr(main_mod.AppServices, "reconcile_endpoint_worker_assignments", fake_reconcile_endpoint_worker_assignments)


def test_module_import_and_status_flow(monkeypatch):
    STORE.clear()
    _patch_services(monkeypatch)
    requirement_installs: list[str] = []

    async def fake_ensure_bundle_requirements_installed(self, bundle_path):
        requirement_installs.append(bundle_path)

    monkeypatch.setattr(main_mod.AppServices, "ensure_bundle_requirements_installed", fake_ensure_bundle_requirements_installed)

    def fake_run_bundle_eval(bundle_path, eval_inputs, num_threads=1, runtime_env=None):
        _ = (bundle_path, eval_inputs, num_threads)
        assert runtime_env == {}
        raise RuntimeError("runtime error")

    monkeypatch.setattr(main_mod, "run_bundle_eval", fake_run_bundle_eval)

    with TestClient(main_mod.app) as client:
        with TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir)
            (bundle_dir / "module.py").write_text(
                "import dspy\nclass S(dspy.Signature):\n  q=dspy.InputField()\n  a=dspy.OutputField()\n"
                "class M(dspy.Module):\n  def forward(self, q: str):\n    return dspy.Prediction(a='x')\n"
                "def build_program():\n  return M()\n",
                encoding="utf-8",
            )
            (bundle_dir / "metric.py").write_text(
                "def judge_metric(example, prediction):\n  return False\n",
                encoding="utf-8",
            )
            (bundle_dir / "bundle.toml").write_text(
                "name='test-bundle'\nversion='0.1.0'\nscore_pass_threshold=0.8\n",
                encoding="utf-8",
            )

            created = client.post(
                "/modules/import",
                json={"source": "git", "source_ref": "repo", "version_hash": "abc"},
            )
            assert created.status_code == 200
            module_id = created.json()["id"]

            validated = client.post(
                f"/modules/{module_id}/validate",
                json={"bundle_path": str(bundle_dir)},
            )
            assert validated.status_code == 200
            assert validated.json()["validation_status"] == "passed"

            smoked = client.post(
                f"/modules/{module_id}/smoke-test",
                json={
                    "bundle_path": str(bundle_dir),
                    "eval_inputs": [{"input": {"q": "hello"}, "label": {"expected": "x"}}],
                    "num_threads": 1,
                },
            )
            assert smoked.status_code == 200
            assert smoked.json()["smoke_status"] == "failed"

            diagnostics = client.get(f"/modules/{module_id}/diagnostics")
            assert diagnostics.status_code == 200
            payload = diagnostics.json()
            assert payload["id"] == module_id
            assert payload["validation_status"] == "passed"
            assert payload["smoke_status"] == "failed"
            assert requirement_installs == [str(bundle_dir)]


def test_module_not_found_paths(monkeypatch):
    STORE.clear()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        validate_missing = client.post(
            "/modules/missing/validate",
            json={"bundle_path": "/tmp/does-not-exist"},
        )
        assert validate_missing.status_code == 404

        smoke_missing = client.post(
            "/modules/missing/smoke-test",
            json={"bundle_path": "/tmp/does-not-exist", "eval_inputs": []},
        )
        assert smoke_missing.status_code == 404

        diag_missing = client.get("/modules/missing/diagnostics")
        assert diag_missing.status_code == 404

        patch_missing = client.patch("/modules/missing", json={"bundle_name": "x", "bundle_version": "1.2.3"})
        assert patch_missing.status_code == 404


def test_module_metadata_can_be_updated(monkeypatch):
    STORE.clear()
    _patch_services(monkeypatch)
    STORE["mod-9"] = {
        "id": "mod-9",
        "status": "validated",
        "validation_status": "passed",
        "smoke_status": "pending",
        "diagnostics": [],
        "bundle_name": "before-name",
        "bundle_version": "0.1.0",
        "source": "upload",
        "source_ref": "/tmp/bundle",
        "checkout_path": "/tmp/bundle",
        "github_secrets_environment_name": None,
        "environment_entries": [],
    }

    with TestClient(main_mod.app) as client:
        response = client.patch("/modules/mod-9", json={"bundle_name": "after-name", "bundle_version": "2.0.0"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["bundle_name"] == "after-name"
        assert payload["bundle_version"] == "2.0.0"


def test_module_environment_can_be_updated(monkeypatch):
    STORE.clear()
    _patch_services(monkeypatch)
    STORE["mod-env"] = {
        "id": "mod-env",
        "status": "validated",
        "validation_status": "passed",
        "smoke_status": "pending",
        "diagnostics": [],
        "bundle_name": "before-name",
        "bundle_version": "0.1.0",
        "source": "upload",
        "source_ref": "/tmp/bundle",
        "checkout_path": "/tmp/bundle",
        "github_secrets_environment_name": None,
        "environment_entries": [],
    }

    with TestClient(main_mod.app) as client:
        response = client.patch(
            "/modules/mod-env",
            json={
                "github_secrets_environment_name": "agentic-chat-prod",
                "environment_entries": [
                    {"key": "AGENTIC_CHAT_ENDPOINT", "value": "https://example.test", "is_secret": True},
                    {"key": "P21_READ_DB_SERVER", "value": "db.example.internal", "is_secret": False},
                ],
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["github_secrets_environment_name"] == "agentic-chat-prod"
        assert payload["environment_entries"][0]["key"] == "AGENTIC_CHAT_ENDPOINT"
        assert payload["environment_entries"][0]["is_secret"] is True


def test_module_import_and_list_include_git_revision_metadata(monkeypatch):
    STORE.clear()
    _patch_services(monkeypatch)

    with TestClient(main_mod.app) as client:
        created = client.post(
            "/modules/import",
            json={
                "source": "github",
                "github_repo_url": "https://github.com/example/demo-bundle",
                "github_branch": "main",
                "github_subpath": "bundles/support",
            },
        )
        assert created.status_code == 200

        listed = client.get("/modules")
        assert listed.status_code == 200
        payload = listed.json()[0]
        assert payload["github_repo_url"] == "https://github.com/example/demo-bundle"
        assert payload["github_branch"] == "main"
        assert payload["github_subpath"] == "bundles/support"
        assert payload["checkout_path"] == "/tmp/dspy-trainer/checkouts/mod-1"
        assert payload["current_commit_sha"] == "abc123"
        assert payload["upstream_commit_sha"] == "abc123"
        assert payload["sync_status"] == "synced"
        assert payload["current_revision"]["id"] == "rev-1"
        assert payload["current_revision"]["commit_sha"] == "abc123"
        assert "github_pat" not in payload


def test_github_import_validation_error_returns_400(monkeypatch):
    STORE.clear()
    _patch_services(monkeypatch)

    async def fake_invalid_import(self, github_repo_url, github_branch, github_subpath=None):
        del self, github_repo_url, github_branch, github_subpath
        raise ValueError("Validation failed with 1 error.")

    monkeypatch.setattr(main_mod.AppServices, "import_github_module", fake_invalid_import)

    with TestClient(main_mod.app) as client:
        response = client.post(
            "/modules/import",
            json={
                "source": "github",
                "github_repo_url": "https://github.com/example/not-a-bundle",
                "github_branch": "main",
            },
        )

    assert response.status_code == 400
    assert response.json()["error"] == "Validation failed with 1 error."


def test_module_sync_status_refresh_and_sync(monkeypatch):
    STORE.clear()
    _patch_services(monkeypatch)
    STORE["mod-1"] = {
        "id": "mod-1",
        "source": "github",
        "github_repo_url": "https://github.com/example/demo-bundle",
        "github_branch": "main",
        "current_commit_sha": "abc123",
        "upstream_commit_sha": "def456",
        "sync_status": "behind",
        "last_sync_error": None,
    }

    with TestClient(main_mod.app) as client:
        cached = client.get("/modules/mod-1/sync-status")
        assert cached.status_code == 200
        assert cached.json()["sync_status"] == "behind"

        refreshed = client.post("/modules/mod-1/sync-status", json={})
        assert refreshed.status_code == 200
        assert refreshed.json()["sync_status"] == "behind"
        assert refreshed.json()["upstream_commit_sha"] == "def456"

        synced = client.post("/modules/mod-1/sync", json={})
        assert synced.status_code == 200
    assert synced.json()["sync_status"] == "synced"
    assert synced.json()["current_commit_sha"] == "def456"


def test_module_revision_history_endpoint(monkeypatch):
    STORE.clear()
    _patch_services(monkeypatch)
    STORE["mod-rev"] = {
        "id": "mod-rev",
        "bundle_name": "repo-bundle",
        "bundle_version": "1.2.3",
        "validation_status": "passed",
        "status": "validated",
        "diagnostics": [],
    }

    with TestClient(main_mod.app) as client:
        response = client.get("/modules/mod-rev/revisions")

    assert response.status_code == 200
    assert response.json()[0]["commit_sha"] == "abc12345"


def test_github_module_metadata_update_is_blocked_when_behind(monkeypatch):
    STORE.clear()
    _patch_services(monkeypatch)
    STORE["mod-1"] = {
        "id": "mod-1",
        "status": "validated",
        "validation_status": "passed",
        "smoke_status": "pending",
        "diagnostics": [],
        "bundle_name": "before-name",
        "bundle_version": "0.1.0",
        "source": "github",
        "source_ref": "/tmp/dspy-trainer/checkouts/mod-1",
        "checkout_path": "/tmp/dspy-trainer/checkouts/mod-1",
        "github_repo_url": "https://github.com/example/demo-bundle",
        "github_branch": "main",
        "current_commit_sha": "abc123",
        "upstream_commit_sha": "def456",
        "sync_status": "behind",
        "last_sync_error": None,
    }

    with TestClient(main_mod.app) as client:
        response = client.patch(
            "/modules/mod-1",
            json={"bundle_name": "after-name", "bundle_version": "2.0.0"},
        )

    assert response.status_code == 409
    assert response.json()["sync_state"]["sync_status"] == "behind"


def test_smoke_test_rerun_overwrites_status(monkeypatch):
    STORE.clear()
    _patch_services(monkeypatch)

    results = [RuntimeError("first fail"), {"score_pct": 100.0, "items": []}]

    def fake_run_bundle_eval(bundle_path, eval_inputs, num_threads=1, runtime_env=None):
        item = results.pop(0)
        assert runtime_env == {}
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(main_mod, "run_bundle_eval", fake_run_bundle_eval)

    with TestClient(main_mod.app) as client:
        module_id = client.post(
            "/modules/import",
            json={"source": "git", "source_ref": "repo", "version_hash": "abc"},
        ).json()["id"]

        first = client.post(f"/modules/{module_id}/smoke-test", json={"bundle_path": "/tmp/a", "eval_inputs": []})
        second = client.post(f"/modules/{module_id}/smoke-test", json={"bundle_path": "/tmp/a", "eval_inputs": []})

        assert first.status_code == 200
        assert first.json()["smoke_status"] == "failed"
        assert second.status_code == 200
        assert second.json()["smoke_status"] == "passed"

        diagnostics = client.get(f"/modules/{module_id}/diagnostics").json()
        assert diagnostics["status"] == "runnable"
        assert diagnostics["smoke_status"] == "passed"
        assert diagnostics["diagnostics"][0]["code"] == "bundle_eval_completed"


def test_sample_bundle_download(monkeypatch):
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        response = client.get("/samples/module-bundle")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert "attachment; filename=\"example-bundle.zip\"" in response.headers["content-disposition"]
        assert len(response.content) > 0


def test_bundle_endpoint_crud_and_key_rotation(monkeypatch):
    STORE.clear()
    ENDPOINTS.clear()
    _patch_services(monkeypatch)
    STORE["mod-endpoint"] = {
        "id": "mod-endpoint",
        "status": "validated",
        "validation_status": "passed",
        "smoke_status": "passed",
        "diagnostics": [],
        "bundle_name": "agentic-chat",
        "bundle_version": "0.1.0",
        "source": "upload",
        "source_ref": "/tmp/bundle",
        "checkout_path": "/tmp/bundle",
        "environment_entries": [],
    }

    with TestClient(main_mod.app) as client:
        created = client.post("/bundle-endpoints", json={"name": "Public API", "module_import_id": "mod-endpoint", "lm_profile_id": "lm-1", "pinned_worker_count": 2})
        assert created.status_code == 200
        assert created.json()["api_key"] == "bep-endpoint-1"
        assert created.json()["lm_profile_id"] == "lm-1"
        assert created.json()["pinned_worker_count"] == 2

        listed = client.get("/bundle-endpoints")
        assert listed.status_code == 200
        assert listed.json()[0]["name"] == "Public API"
        assert listed.json()[0]["module_import_id"] == "mod-endpoint"

        fetched = client.get("/bundle-endpoints/endpoint-1")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == "endpoint-1"

        updated = client.patch("/bundle-endpoints/endpoint-1", json={"name": "Customer stream", "module_import_id": "mod-endpoint", "lm_profile_id": "lm-1", "pinned_worker_count": 3})
        assert updated.status_code == 200
        assert updated.json()["name"] == "Customer stream"
        assert updated.json()["pinned_worker_count"] == 3

        rotated = client.post("/bundle-endpoints/endpoint-1/regenerate-key")
        assert rotated.status_code == 200
        assert rotated.json()["api_key"] == "rotated-endpoint-1"

        deleted = client.delete("/bundle-endpoints/endpoint-1")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True


def test_bundle_endpoint_sync_and_stream_invocation(monkeypatch):
    STORE.clear()
    ENDPOINTS.clear()
    _patch_services(monkeypatch)
    STORE["mod-endpoint"] = {
        "id": "mod-endpoint",
        "status": "validated",
        "validation_status": "passed",
        "smoke_status": "passed",
        "diagnostics": [],
        "bundle_name": "agentic-chat",
        "bundle_version": "0.1.0",
        "source": "upload",
        "source_ref": "/tmp/bundle",
        "checkout_path": "/tmp/bundle",
        "environment_entries": [],
    }
    ENDPOINTS["endpoint-1"] = {
        "id": "endpoint-1",
        "module_import_id": "mod-endpoint",
        "pinned_worker_count": 1,
        "name": "Customer stream",
        "key_preview": "abc123",
        "api_key": "secret-key",
        "created_at": None,
        "updated_at": None,
    }

    with TestClient(main_mod.app) as client:
        sync_response = client.post(
            "/bundle-endpoints/endpoint-1/invoke",
            json={"question": "hello"},
            headers={"Authorization": "Bearer secret-key"},
        )
        assert sync_response.status_code == 200
        assert sync_response.json()["echo"]["question"] == "hello"

        with client.stream(
            "POST",
            "/bundle-endpoints/endpoint-1/stream",
            json={"question": "hello"},
            headers={"Authorization": "Bearer secret-key"},
        ) as stream_response:
            assert stream_response.status_code == 200
            body = "\n".join(stream_response.iter_text())

        assert "event: delta" in body
        assert '"chunk": 1' in body
        assert "event: final" in body
        assert '"done": true' in body


def test_endpoint_workers_listing(monkeypatch):
    STORE.clear()
    ENDPOINTS.clear()
    _patch_services(monkeypatch)

    with TestClient(main_mod.app) as client:
        response = client.get("/endpoint-workers")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_workers"] == 2
    assert payload["items"][0]["worker_id"] == "endpoint-worker-1"
