import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_mod
from app.services import ReadinessStatus


STORE: dict[str, dict] = {}


async def fake_connect(self):
    return None


async def fake_disconnect(self):
    return None


async def fake_readiness(self):
    return ReadinessStatus(postgres=True, redis=True, mlflow=True, litellm=True)


async def fake_create_module_import(self, source, source_ref, version_hash):
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
    }
    return {"id": module_id, "status": "imported"}


async def fake_set_validation_status(self, module_id, status, diagnostics):
    if module_id not in STORE:
        return False
    STORE[module_id]["validation_status"] = status
    STORE[module_id]["status"] = "validated" if status == "passed" else "validation_failed"
    STORE[module_id]["diagnostics"] = diagnostics
    return True


async def fake_set_smoke_status(self, module_id, status, diagnostics):
    if module_id not in STORE:
        return False
    STORE[module_id]["smoke_status"] = status
    if status == "running":
        STORE[module_id]["status"] = "smoke_testing"
    else:
        STORE[module_id]["status"] = "runnable" if status == "passed" else "smoke_failed"
    STORE[module_id]["diagnostics"] = diagnostics
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


def _patch_services(monkeypatch):
    monkeypatch.setenv("DSPY_TRAINER_POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/dspy_trainer")
    monkeypatch.setattr(main_mod.AppServices, "connect", fake_connect)
    monkeypatch.setattr(main_mod.AppServices, "disconnect", fake_disconnect)
    monkeypatch.setattr(main_mod.AppServices, "readiness", fake_readiness)
    monkeypatch.setattr(main_mod.AppServices, "create_module_import", fake_create_module_import)
    monkeypatch.setattr(main_mod.AppServices, "set_validation_status", fake_set_validation_status)
    monkeypatch.setattr(main_mod.AppServices, "set_smoke_status", fake_set_smoke_status)
    monkeypatch.setattr(main_mod.AppServices, "get_diagnostics", fake_get_diagnostics)
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
                "JUDGE_INSTRUCTIONS='p/f'\ndef judge_metric(example, prediction):\n  return False\n",
                encoding="utf-8",
            )
            (bundle_dir / "bundle.toml").write_text(
                "name='test-bundle'\nversion='0.1.0'\nlm_target='gpt-4.1-mini'\n",
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


def test_smoke_test_rerun_overwrites_status(monkeypatch):
    STORE.clear()
    _patch_services(monkeypatch)

    results = [RuntimeError("first fail"), {"score_pct": 100.0, "items": [], "judge_instructions": "ok"}]

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
                    "JUDGE_INSTRUCTIONS='ok'\ndef judge_metric(example, prediction, trace=None):\n  return True\n",
                )
                archive.writestr(
                    "example-bundle/bundle.toml",
                    "name='zip-bundle'\nversion='0.1.0'\nlm_target='gpt-4.1-mini'\n",
                )

            with zip_path.open("rb") as handle:
                response = client.post(
                    f"/modules/{module_id}/validate-upload",
                    files={"bundle": ("bundle.zip", handle, "application/zip")},
                )

        assert response.status_code == 200
        assert response.json()["validation_status"] == "passed"
