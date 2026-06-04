import asyncio
import sys
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services import AppServices


class _Conn:
    def __init__(self, state):
        self.state = state

    async def fetchrow(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if normalized == "select source_ref, checkout_path, current_commit_sha from module_imports where id = $1":
            module = self.state.get(str(params[0]))
            if module is None:
                return None
            return {
                "source_ref": module["source_ref"],
                "checkout_path": module.get("checkout_path"),
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


def test_build_module_payload_includes_github_and_revision_metadata():
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))

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
