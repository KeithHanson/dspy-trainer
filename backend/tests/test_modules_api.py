import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_mod
from app.services import ModuleSyncError, ReadinessStatus


STORE: dict[str, dict] = {}


async def fake_connect(self):
    return None


async def fake_disconnect(self):
    return None


async def fake_readiness(self):
    return ReadinessStatus(postgres=True, redis=True, mlflow=True, litellm=True)


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


async def fake_get_module(self, module_id):
    return STORE.get(module_id)


async def fake_list_modules(self):
    return list(STORE.values())


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


def test_module_import_and_status_flow(monkeypatch):
    STORE.clear()
    _patch_services(monkeypatch)
    def fake_run_bundle_eval(bundle_path, eval_inputs, num_threads=1):
        _ = (bundle_path, eval_inputs, num_threads)
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
    }

    with TestClient(main_mod.app) as client:
        response = client.patch("/modules/mod-9", json={"bundle_name": "after-name", "bundle_version": "2.0.0"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["bundle_name"] == "after-name"
        assert payload["bundle_version"] == "2.0.0"


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

    def fake_run_bundle_eval(bundle_path, eval_inputs, num_threads=1):
        item = results.pop(0)
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


def test_module_validate_upload_zip(monkeypatch):
    STORE.clear()
    _patch_services(monkeypatch)

    with TestClient(main_mod.app) as client:
        module_id = client.post(
            "/modules/import",
            json={"source": "upload", "source_ref": "bundle.zip", "version_hash": "zip-1"},
        ).json()["id"]

        with TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "bundle.zip"
            with ZipFile(zip_path, mode="w", compression=ZIP_DEFLATED) as archive:
                archive.writestr(
                    "example-bundle/module.py",
                    "import dspy\nclass Sig(dspy.Signature):\n  q=dspy.InputField()\n  a=dspy.OutputField()\n"
                    "class Agent(dspy.Module):\n  def forward(self, q: str):\n    return dspy.Prediction(a='x')\n"
                    "def build_program():\n  return Agent()\n",
                )
                archive.writestr(
                    "example-bundle/metric.py",
                    "def judge_metric(example, prediction, trace=None):\n  return True\n",
                )
                archive.writestr(
                    "example-bundle/bundle.toml",
                    "name='zip-bundle'\nversion='0.1.0'\nscore_pass_threshold=0.8\n",
                )

            with zip_path.open("rb") as handle:
                response = client.post(
                    f"/modules/{module_id}/validate-upload",
                    files={"bundle": ("bundle.zip", handle, "application/zip")},
                )

        assert response.status_code == 200
        assert response.json()["validation_status"] == "passed"
