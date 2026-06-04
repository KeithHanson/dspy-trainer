import asyncio
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services import AppServices


class _Conn:
    def __init__(self, state):
        self.state = state

    async def fetchrow(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if normalized == "select source_ref from module_imports where id = $1":
            module = self.state.get(str(params[0]))
            if module is None:
                return None
            return {"source_ref": module["source_ref"]}
        return None

    async def execute(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if normalized.startswith("update module_imports set bundle_name = coalesce($2, bundle_name), bundle_version = coalesce($3, bundle_version), updated_at = now() where id = $1"):
            module = self.state.get(str(params[0]))
            if module is not None:
                module["bundle_name"] = params[1]
                module["bundle_version"] = params[2]
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
        'name = "before-name"\nversion = "0.1.0"\nlm_target = "gpt-4.1-mini"\nscore_pass_threshold = 0.8\n',
        encoding="utf-8",
    )
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    state = {
        "mod-1": {
            "source_ref": str(bundle_root),
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
