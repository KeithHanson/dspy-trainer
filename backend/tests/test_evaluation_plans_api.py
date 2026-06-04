import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_mod


PLANS: dict[str, dict] = {}
NEXT_PLAN_ID = 1


async def fake_connect(self):
    return None


async def fake_disconnect(self):
    return None


async def fake_create_evaluation_plan(self, project_id, scenario_id, dataset_version, name, runs_per_question, max_workers, module_import_id, lm_profile_id, eval_inputs):
    global NEXT_PLAN_ID
    plan_id = f"eval-plan-{NEXT_PLAN_ID}"
    NEXT_PLAN_ID += 1
    plan = {
        "id": plan_id,
        "project_id": project_id,
        "scenario_id": scenario_id,
        "dataset_version": dataset_version,
        "name": name,
        "runs_per_question": runs_per_question,
        "max_workers": max_workers,
        "module_import_id": module_import_id,
        "lm_profile_id": lm_profile_id,
        "eval_inputs": eval_inputs,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    PLANS[plan_id] = plan
    return plan


async def fake_list_evaluation_plans(self):
    return list(PLANS.values())[::-1]


async def fake_get_evaluation_plan(self, evaluation_plan_id):
    return PLANS.get(evaluation_plan_id)


async def fake_delete_evaluation_plan(self, evaluation_plan_id):
    if evaluation_plan_id not in PLANS:
        return False
    del PLANS[evaluation_plan_id]
    return True


async def fake_update_evaluation_plan(self, evaluation_plan_id, project_id, scenario_id, dataset_version, name, runs_per_question, max_workers, module_import_id, lm_profile_id, eval_inputs):
    plan = PLANS.get(evaluation_plan_id)
    if plan is None:
        return None
    plan.update(
        {
            "project_id": project_id,
            "scenario_id": scenario_id,
            "dataset_version": dataset_version,
            "name": name,
            "runs_per_question": runs_per_question,
            "max_workers": max_workers,
            "module_import_id": module_import_id,
            "lm_profile_id": lm_profile_id,
            "eval_inputs": eval_inputs,
        }
    )
    return plan


async def fake_generate_evaluation_rows(self, lm_profile_id, operator_prompt, existing_rows, max_rows):
    assert lm_profile_id == "lm-1"
    assert operator_prompt == "Generate refund cases"
    assert max_rows == 2
    assert len(existing_rows) == 1
    return {
        "items": [
            {"input": {"question": "Customer asks about refund timeline"}, "label": {"expected": "Explain the refund timing policy."}},
            {"input": {"question": "Customer wants a damaged-item refund"}, "label": {"expected": "Ask for evidence and explain the damaged-item refund flow."}},
        ],
        "attempts": 2,
    }


def _patch_services(monkeypatch):
    monkeypatch.setenv("DSPY_TRAINER_POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/dspy_trainer")
    monkeypatch.setattr(main_mod.AppServices, "connect", fake_connect)
    monkeypatch.setattr(main_mod.AppServices, "disconnect", fake_disconnect)
    monkeypatch.setattr(main_mod.AppServices, "create_evaluation_plan", fake_create_evaluation_plan)
    monkeypatch.setattr(main_mod.AppServices, "list_evaluation_plans", fake_list_evaluation_plans)
    monkeypatch.setattr(main_mod.AppServices, "get_evaluation_plan", fake_get_evaluation_plan)
    monkeypatch.setattr(main_mod.AppServices, "delete_evaluation_plan", fake_delete_evaluation_plan)
    monkeypatch.setattr(main_mod.AppServices, "update_evaluation_plan", fake_update_evaluation_plan)
    monkeypatch.setattr(main_mod.AppServices, "generate_evaluation_rows", fake_generate_evaluation_rows)


def _reset_state():
    global NEXT_PLAN_ID
    PLANS.clear()
    NEXT_PLAN_ID = 1


def test_create_list_get_evaluation_plans(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        created = client.post(
            "/evaluation-plans",
            json={
                "project_id": "proj-1",
                "scenario_id": "scn-1",
                "dataset_version": "v1",
                "name": "Support triage regression",
                "runs_per_question": 3,
                "max_workers": 8,
                "module_import_id": "mod-1",
                "eval_inputs": [{"input": {"question": "q1"}, "label": {"expected": "a1"}}],
            },
        )
        assert created.status_code == 200
        assert created.json()["name"] == "Support triage regression"
        assert created.json()["runs_per_question"] == 3

        listed = client.get("/evaluation-plans")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == created.json()["id"]

        fetched = client.get(f"/evaluation-plans/{created.json()['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["max_workers"] == 8


def test_get_evaluation_plan_not_found(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        missing = client.get("/evaluation-plans/missing")
        assert missing.status_code == 404


def test_delete_evaluation_plan(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        created = client.post(
            "/evaluation-plans",
            json={
                "project_id": "proj-1",
                "scenario_id": "scn-1",
                "dataset_version": "v1",
                "name": "Delete me",
                "runs_per_question": 1,
                "max_workers": 1,
                "module_import_id": "mod-1",
                "eval_inputs": [],
            },
        )
        assert created.status_code == 200
        plan_id = created.json()["id"]

        deleted = client.delete(f"/evaluation-plans/{plan_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        missing = client.delete(f"/evaluation-plans/{plan_id}")
        assert missing.status_code == 404


def test_update_evaluation_plan(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        created = client.post(
            "/evaluation-plans",
            json={
                "project_id": "proj-1",
                "scenario_id": "scn-1",
                "dataset_version": "v1",
                "name": "Initial",
                "runs_per_question": 1,
                "max_workers": 1,
                "module_import_id": "mod-1",
                "eval_inputs": [],
            },
        )
        plan_id = created.json()["id"]

        updated = client.patch(
            f"/evaluation-plans/{plan_id}",
            json={
                "project_id": "proj-1",
                "scenario_id": "scn-1",
                "dataset_version": "v2",
                "name": "Updated",
                "runs_per_question": 3,
                "max_workers": 4,
                "module_import_id": "mod-2",
                "lm_profile_id": "lm-2",
                "eval_inputs": [{"input": {"question": "q"}, "label": {"expected": "a"}}],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["id"] == plan_id
        assert updated.json()["name"] == "Updated"
        assert updated.json()["dataset_version"] == "v2"


def test_generate_evaluation_plan_rows(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        response = client.post(
            "/evaluation-plans/generate-rows",
            json={
                "lm_profile_id": "lm-1",
                "operator_prompt": "Generate refund cases",
                "existing_rows": [{"input": {"question": "Example q"}, "label": {"expected": "Example a"}}],
                "max_rows": 2,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["attempts"] == 2
        assert len(payload["items"]) == 2
