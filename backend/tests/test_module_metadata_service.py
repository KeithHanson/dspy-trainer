import asyncio
import os
import sys
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services import AppServices, _classify_sync_status


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
